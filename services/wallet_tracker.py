import threading
import logging
from datetime import datetime, timezone
from agents.shared.python.db import (
    get_connection, save_trader_transaction, update_wallet_stats
)

logger = logging.getLogger("WalletTracker")
_MIN_TRADE_USD = 500.0  # порог "крупной" сделки

def ingest_trades(market_id: str, trades: list, positions: list) -> int:
    """
    Сохраняет крупные сделки из onchain_trades в trader_transactions.
    Вызывается из engine.py ПОСЛЕ analyze_smart_money().
    Возвращает кол-во сохранённых записей.
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
        wr = row["wins"] / row["total"] if row["total"] > 0 else 0.0
        update_wallet_stats(row["wallet_address"], round(wr, 3), row["total_vol"] or 0.0)
        updated += 1
    logger.info(f"[WalletTracker] Пересчитан win_rate для {updated} кошельков")
    return updated
