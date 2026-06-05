import threading
import logging
from datetime import datetime, timezone
from agents.shared.python.db import (
    get_connection, save_trader_transaction, update_wallet_stats
)

logger = logging.getLogger("NexusPolyBot.WalletTracker")
_MIN_TRADE_USD = 500.0  # порог "крупной" сделки

def ingest_trades(market_id: str, trades: list, positions: list) -> int:
    """
    Сохраняет крупные сделки из onchain_trades в trader_transactions.
    Вызывается из engine.py ПОСЛЕ analyze_smart_money().
    Возвращает кол-во сохранённых записей.

    IMPORTANT: Данная функция является СИНХРОННОЙ и выполняет блокирующие
    дисковые SQL-запросы к базе данных SQLite. Если вызов происходит
    внутри асинхронного контекста (asyncio), её необходимо оборачивать
    в `asyncio.to_thread` во избежание блокирования event loop.
    """
    saved = 0
    for trade in trades:
        addr = trade.get("maker_address") or trade.get("taker_address", "")
        if not addr:
            continue
        try:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0.5))
            usd = size * price
        except (ValueError, TypeError):
            continue
        if usd < _MIN_TRADE_USD:
            continue
        outcome = "YES" if trade.get("outcome_index", 0) == 0 else "NO"
        save_trader_transaction(addr, market_id, outcome, round(usd, 2), price)
        saved += 1
    logger.debug(f"[WalletTracker] Сохранено {saved} сделок для рынка {market_id}")
    return saved


def binomial_coefficient(n: int, k: int) -> int:
    if k < 0 or k > n: return 0
    if k == 0 or k == n: return 1
    k = min(k, n - k)
    c = 1
    for i in range(k):
        c = c * (n - i) // (i + 1)
    return c


def calculate_binomial_p_value(n: int, k: int, p_base: float = 0.5) -> float:
    """Вычисляет одностороннее binomial p-value для k или более успехов в n испытаниях."""
    if n <= 0 or k <= 0: return 1.0
    if k > n: return 0.0
    p_val = 0.0
    for i in range(k, n + 1):
        p_val += binomial_coefficient(n, i) * (p_base ** i) * ((1.0 - p_base) ** (n - i))
    return p_val


def recalculate_win_rates() -> int:
    """
    Чистый SQL-пересчёт win_rate без LLM.
    Сравнивает outcome транзакций с результатом markets.
    Вызывается из cron (раз в 24ч).
    Возвращает кол-во обновлённых кошельков.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.wallet_address,
                   COUNT(*) AS total,
                   SUM(CASE WHEN t.outcome = m.outcome THEN 1 ELSE 0 END) AS wins,
                   SUM(t.amount_usd) AS total_vol
            FROM trader_transactions t
            JOIN markets m ON t.market_id = m.id
            WHERE m.outcome IS NOT NULL AND m.outcome != ''
            GROUP BY t.wallet_address
            HAVING total >= 3
        """).fetchall()

    updated = 0
    for row in rows:
        total = row["total"]
        wins = row["wins"] or 0
        wr = wins / total if total > 0 else 0.0
        p_val = calculate_binomial_p_value(total, wins)
        is_insider = (total >= 15 and p_val < 0.05)
        update_wallet_stats(row["wallet_address"], round(wr, 3), row["total_vol"] or 0.0, is_insider)
        updated += 1
    logger.info(f"[WalletTracker] Пересчитан win_rate для {updated} кошельков")
    return updated
