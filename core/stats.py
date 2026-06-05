# core/stats.py
"""
Биномиальный p-value без scipy.
Используется для определения статистической значимости win_rate кошелька.
"""
import math
from typing import Tuple


def _log_binom_coeff(n: int, k: int) -> float:
    """log C(n,k) через логарифм гамма-функции — избегает переполнения."""
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def binomial_pvalue(n: int, k: int, p0: float = 0.5) -> float:
    """
    Односторонний биномиальный p-value: P(X >= k | n, p0).
    
    Нулевая гипотеза H0: победы трейдера случайны с вероятностью p0.
    Малый p-value (< 0.05) означает: случайность маловероятна → инсайдер.

    :param n: Общее число завершённых сделок
    :param k: Число побед (profitable trades)
    :param p0: Вероятность случайной победы (default=0.5)
    :return: p-value ∈ [0.0, 1.0]
    :raises ValueError: при некорректных параметрах
    """
    if not (0 < p0 < 1):
        raise ValueError(f"p0 должно быть в (0, 1), получено: {p0}")
    if n < 0 or k < 0:
        raise ValueError(f"n и k должны быть неотрицательными, получено n={n}, k={k}")
    if k > n:
        raise ValueError(f"k не может превышать n: k={k}, n={n}")
    if n == 0:
        return 1.0

    log_p0 = math.log(p0)
    log_q0 = math.log(1 - p0)

    terms = []
    for i in range(k, n + 1):
        log_term = _log_binom_coeff(n, i) + i * log_p0 + (n - i) * log_q0
        terms.append(math.exp(log_term))
    pvalue = math.fsum(terms)

    return min(pvalue, 1.0)  # плавающая точка может дать 1.0000000002


def is_statistically_significant(
    n: int, k: int,
    min_trades: int = 15,
    alpha: float = 0.05,
    min_win_rate: float = 0.5
) -> Tuple[bool, float]:
    """
    Комплексная проверка: инсайдер ли кошелёк?

    :return: (is_insider: bool, p_value: float)
    """
    if n < min_trades:
        return False, 1.0
    if k / n <= min_win_rate:
        return False, 1.0
    pv = binomial_pvalue(n, k, p0=min_win_rate)
    return pv < alpha, pv
