import math
import pytest
from core.stats import binomial_pvalue, is_statistically_significant


class TestBinomialPvalue:
    def test_certain_win_returns_near_zero(self):
        """Все 20 побед из 20 → p-value ≈ 1e-6."""
        pv = binomial_pvalue(20, 20, p0=0.5)
        assert pv < 1e-5, f"Ожидался очень малый p-value, получено {pv}"

    def test_half_wins_returns_near_half(self):
        """10 побед из 20 — типичный случай → p-value ≈ 0.59 (правый хвост)."""
        pv = binomial_pvalue(20, 10, p0=0.5)
        assert 0.4 < pv <= 1.0, f"Ожидался высокий p-value, получено {pv}"

    def test_exactly_threshold(self):
        """15 побед из 20 (75%) → должен быть значимым при alpha=0.05."""
        pv = binomial_pvalue(20, 15, p0=0.5)
        assert pv < 0.05, f"75% win rate из 20 должен быть значимым, получено {pv}"

    def test_zero_trades_returns_one(self):
        assert binomial_pvalue(0, 0) == 1.0

    def test_k_equals_n_small(self):
        """5 из 5 → p = 0.5^5 = 0.03125."""
        pv = binomial_pvalue(5, 5, p0=0.5)
        assert abs(pv - 0.03125) < 1e-9

    def test_invalid_k_greater_than_n(self):
        with pytest.raises(ValueError, match="k не может превышать n"):
            binomial_pvalue(10, 11)

    def test_invalid_p0(self):
        with pytest.raises(ValueError):
            binomial_pvalue(10, 8, p0=1.5)

    def test_result_bounded(self):
        """p-value всегда в [0, 1]."""
        for n, k in [(1, 1), (100, 60), (1000, 900), (15, 15)]:
            pv = binomial_pvalue(n, k)
            assert 0.0 <= pv <= 1.0, f"p-value вне диапазона при n={n}, k={k}: {pv}"

    def test_scipy_equivalence(self):
        """Проверяем совпадение с scipy (если доступна) — reference test."""
        try:
            from scipy.stats import binom_test  # type: ignore
            for n, k in [(20, 15), (50, 35), (15, 12)]:
                our = binomial_pvalue(n, k, 0.5)
                ref = binom_test(k, n, 0.5, alternative='greater')
                assert abs(our - ref) < 1e-9, \
                    f"Расхождение со scipy при n={n}, k={k}: our={our}, scipy={ref}"
        except ImportError:
            pytest.skip("scipy не установлен — reference test пропущен")


class TestIsStatisticallySignificant:
    def test_below_min_trades_not_insider(self):
        significant, pv = is_statistically_significant(14, 12, min_trades=15)
        assert not significant
        assert pv == 1.0

    def test_exactly_min_trades_high_wr(self):
        """15 сделок, 13 побед (86.7%) → должен быть инсайдером."""
        significant, pv = is_statistically_significant(15, 13, min_trades=15, alpha=0.05)
        assert significant, f"Ожидался инсайдер, p={pv}"
        assert pv < 0.05

    def test_min_trades_low_wr_not_insider(self):
        """15 сделок, 8 побед (53%) — недостаточно."""
        significant, pv = is_statistically_significant(15, 8)
        assert not significant

    def test_win_rate_exactly_at_threshold(self):
        """win_rate = p0 → не инсайдер (нет превышения)."""
        significant, pv = is_statistically_significant(20, 10, min_win_rate=0.5)
        assert not significant

    def test_high_n_moderate_wr_significant(self):
        """100 сделок, 60 побед (60%) → значимо при большой выборке."""
        significant, pv = is_statistically_significant(100, 60, min_trades=15)
        assert significant, f"60% из 100 должен быть значимым, p={pv}"
