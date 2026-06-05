# core/insider_filter.py
"""
Биномиальный фильтр инсайдеров.
Кошелёк получает статус is_insider=True только при:
  - n_trades >= MIN_TRADES
  - win_rate > WIN_RATE_THRESHOLD
  - p_value < ALPHA (статистически значимо)
"""
import logging
from dataclasses import dataclass
from core.stats import is_statistically_significant

logger = logging.getLogger("NexusPolyBot.InsiderFilter")

MIN_TRADES: int = 15
ALPHA: float = 0.05
WIN_RATE_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class InsiderVerdict:
    address: str
    n_trades: int
    n_wins: int
    win_rate: float
    p_value: float
    is_insider: bool
    reason: str


def evaluate_wallet(address: str, n_trades: int, n_wins: int) -> InsiderVerdict:
    """
    Оценивает кошелёк по биномиальному критерию.

    :param address: Адрес кошелька
    :param n_trades: Число завершённых сделок с известным исходом
    :param n_wins: Число прибыльных сделок
    :return: InsiderVerdict с вердиктом и p-value
    """
    if n_trades == 0:
        return InsiderVerdict(address, 0, 0, 0.0, 1.0, False,
                              "Нет завершённых сделок")

    win_rate = n_wins / n_trades

    is_insider, pv = is_statistically_significant(
        n=n_trades, k=n_wins,
        min_trades=MIN_TRADES,
        alpha=ALPHA,
        min_win_rate=WIN_RATE_THRESHOLD,
    )

    if n_trades < MIN_TRADES:
        reason = f"Недостаточно сделок: {n_trades} < {MIN_TRADES}"
    elif win_rate <= WIN_RATE_THRESHOLD:
        reason = f"Win rate {win_rate:.1%} не превышает порог {WIN_RATE_THRESHOLD:.0%}"
    elif not is_insider:
        reason = f"p-value={pv:.4f} ≥ α={ALPHA} (результат статистически незначим)"
    else:
        reason = (f"✅ Инсайдер подтверждён: n={n_trades}, "
                  f"wins={n_wins}, WR={win_rate:.1%}, p={pv:.4f}")

    logger.info(f"[InsiderFilter] {address[:10]}... → {reason}")
    return InsiderVerdict(address, n_trades, n_wins, win_rate, pv, is_insider, reason)


def recalculate_all_insiders() -> list[InsiderVerdict]:
    """
    Пересчитывает статус is_insider для всех кошельков в БД.
    Запускается периодически (например, раз в час).
    """
    from agents.shared.python.db import (
        get_wallets_for_pvalue_recalc, update_wallet_pvalue
    )

    wallets = get_wallets_for_pvalue_recalc()
    verdicts = []

    for w in wallets:
        verdict = evaluate_wallet(
            address=w["address"],
            n_trades=w.get("n_trades") or w.get("tx_count", 0),
            n_wins=w.get("n_wins", 0),
        )
        update_wallet_pvalue(
            address=verdict.address,
            n_trades=verdict.n_trades,
            n_wins=verdict.n_wins,
            p_value=verdict.p_value,
            is_insider=verdict.is_insider,
        )
        verdicts.append(verdict)

    insiders_found = sum(1 for v in verdicts if v.is_insider)
    logger.info(f"[InsiderFilter] Пересчёт завершён: {insiders_found}/{len(verdicts)} инсайдеров")
    return verdicts
