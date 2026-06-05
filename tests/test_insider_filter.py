import pytest
from unittest.mock import patch, MagicMock
from core.insider_filter import evaluate_wallet, recalculate_all_insiders, InsiderVerdict


class TestEvaluateWallet:
    def test_no_trades_not_insider(self):
        v = evaluate_wallet("0xABC", 0, 0)
        assert not v.is_insider
        assert v.p_value == 1.0
        assert "Нет завершённых" in v.reason

    def test_below_min_trades_not_insider(self):
        v = evaluate_wallet("0xABC", 14, 13)
        assert not v.is_insider
        assert "Недостаточно" in v.reason

    def test_confirmed_insider(self):
        """15 сделок, 13 побед (86.7%) → инсайдер."""
        v = evaluate_wallet("0xABC", 15, 13)
        assert v.is_insider
        assert v.p_value < 0.05
        assert "✅" in v.reason

    def test_high_n_moderate_wr_insider(self):
        """100 сделок, 65 побед → инсайдер."""
        v = evaluate_wallet("0xDEF", 100, 65)
        assert v.is_insider
        assert v.p_value < 0.05

    def test_random_performance_not_insider(self):
        """20 сделок, 11 побед (55%) → не инсайдер."""
        v = evaluate_wallet("0xGHI", 20, 11)
        assert not v.is_insider

    def test_exactly_50pct_not_insider(self):
        """Ровно 50% — не превышает порог."""
        v = evaluate_wallet("0xJKL", 30, 15)
        assert not v.is_insider
        assert "Win rate" in v.reason

    def test_verdict_is_frozen(self):
        """InsiderVerdict — иммутабельный датакласс."""
        v = evaluate_wallet("0xMNO", 20, 16)
        with pytest.raises((AttributeError, TypeError)):
            v.is_insider = True  # type: ignore

    def test_pvalue_uses_historical_trades_not_current_market(self):
        """n_trades должен браться из истории кошелька, а не из текущего рынка."""
        # Кит с 90% историческим WR на 20 сделках — должен быть инсайдером
        historical_trades = 20
        historical_wins = 18
        v = evaluate_wallet("0xABC", historical_trades, historical_wins)
        assert v.is_insider, f"Ожидался инсайдер по истории, p={v.p_value}"

        # Тот же кит, но только 2 сделки на текущем рынке — НЕ должно использоваться
        current_market_trades = 2
        current_wins = int(0.9 * current_market_trades)  # = 1
        v_wrong = evaluate_wallet("0xABC", current_market_trades, current_wins)
        assert not v_wrong.is_insider, \
            "BUG-SM-01: p-value вычислен по 2 сделкам текущего рынка, а не по 20 историческим"


class TestRecalculateAllInsiders:
    def test_recalculate_updates_db(self):
        mock_wallets = [
            {"address": "0xAAA", "n_trades": 20, "n_wins": 17, "tx_count": 20},
            {"address": "0xBBB", "n_trades": 10, "n_wins": 6, "tx_count": 10},
        ]
        with patch("agents.shared.python.db.get_wallets_for_pvalue_recalc",
                   return_value=mock_wallets), \
             patch("agents.shared.python.db.update_wallet_pvalue") as mock_update:
            verdicts = recalculate_all_insiders()

        assert len(verdicts) == 2
        assert mock_update.call_count == 2

        # 0xAAA: 20 сделок, 85% → инсайдер
        v_aaa = next(v for v in verdicts if v.address == "0xAAA")
        assert v_aaa.is_insider

        # 0xBBB: 10 сделок < 15 → не инсайдер
        v_bbb = next(v for v in verdicts if v.address == "0xBBB")
        assert not v_bbb.is_insider

    def test_recalculate_empty_wallets(self):
        with patch("agents.shared.python.db.get_wallets_for_pvalue_recalc", return_value=[]), \
             patch("agents.shared.python.db.update_wallet_pvalue") as mock_update:
            verdicts = recalculate_all_insiders()
        assert verdicts == []
        mock_update.assert_not_called()

    def test_recalculate_uses_computed_wins_when_n_wins_is_zero(self):
        """Если n_wins=0 в БД, берём computed_wins из JOIN."""
        mock_wallets = [{
            "address": "0xAAA",
            "n_trades": 0,    # не обновлено через update_wallet_pvalue
            "n_wins": 0,      # DEFAULT 0
            "tx_count": 20,
            "computed_wins": 17,  # реальные победы из JOIN
        }]
        with patch("agents.shared.python.db.get_wallets_for_pvalue_recalc",
                   return_value=mock_wallets), \
             patch("agents.shared.python.db.update_wallet_pvalue") as mock_upd:
            verdicts = recalculate_all_insiders()

        v = verdicts[0]
        assert v.n_wins == 17, f"BUG-IF-01: n_wins должен браться из computed_wins, получено {v.n_wins}"
        assert v.is_insider, f"17/20 должен быть инсайдером, p={v.p_value}"
