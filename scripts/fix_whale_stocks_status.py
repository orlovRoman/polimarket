import os
import sys
import sqlite3
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WhaleFix")

sys.path.append('.')
from services.polymarket_client import get_market_resolution

DB_PATH = "vault/database.sqlite"

def _process_market(cursor, conn, market_id, title, db_actual_outcome) -> bool:
    try:
        # Получаем реальный резолв из API (или None, если еще не разрешен/в процессе UMA)
        real_resolution = get_market_resolution(market_id)
        
        needs_revert = False
        
        if real_resolution is None:
            logger.info(f"Market {market_id} '{title[:50]}...' is NOT resolved on Polymarket yet. Reverting...")
            needs_revert = True
        elif real_resolution != db_actual_outcome:
            logger.info(f"Market {market_id} '{title[:50]}...' resolved to {real_resolution}, but DB says {db_actual_outcome}. Reverting...")
            needs_revert = True
            
        if needs_revert:
            history_row = cursor.execute(
                "SELECT bought_price, bought_at, bet_size_usdc FROM whale_virtual_trades_history WHERE market_id = ?", 
                (market_id,)
            ).fetchone()

            v_bought = history_row['bought_price'] if history_row else None
            v_bought_at = history_row['bought_at'] if history_row else None
            v_bet_size = history_row['bet_size_usdc'] if history_row else None

            cursor.execute("""
                UPDATE whale_stocks_monitoring
                SET status = 'ACTIVE',
                    actual_outcome = NULL,
                    resolved_at = NULL,
                    virtual_bought_price = CASE WHEN virtual_bought_price IS NULL THEN ? ELSE virtual_bought_price END,
                    virtual_bought_at = CASE WHEN virtual_bought_at IS NULL THEN ? ELSE virtual_bought_at END,
                    bet_size_usdc = CASE WHEN bet_size_usdc IS NULL THEN ? ELSE bet_size_usdc END
                WHERE market_id = ?
            """, (v_bought, v_bought_at, v_bet_size, market_id))

            if history_row:
                cursor.execute("DELETE FROM whale_virtual_trades_history WHERE market_id = ?", (market_id,))
                logger.info(f"  Restored bought_price: {v_bought}, deleted from history.")
            else:
                logger.info("  No trade history found.")

            conn.commit()
            return True
            
        # Уберем 'YES' из таблицы markets в любом случае
        cursor.execute("UPDATE markets SET outcome = NULL WHERE outcome = 'YES' AND id = ?", (market_id,))
        conn.commit()
            
    except Exception as e:
        logger.error(f"Error processing market {market_id}: {e}")
    return False

def main():
    if not os.path.exists(DB_PATH):
        logger.error(f"Database not found at {DB_PATH}")
        return

    logger.info("Connecting to database...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows = cursor.execute("SELECT market_id, title, actual_outcome FROM whale_stocks_monitoring WHERE status = 'RESOLVED'").fetchall()
    logger.info(f"Found {len(rows)} resolved whale stocks to check.")

    fixed_count = 0

    for row in rows:
        market_id = row['market_id']
        title = row['title']
        actual_outcome = row['actual_outcome']

        if _process_market(cursor, conn, market_id, title, actual_outcome):
            fixed_count += 1

        time.sleep(0.5)

    logger.info(f"Check complete. Reverted {fixed_count} prematurely resolved markets to ACTIVE.")

if __name__ == "__main__":
    main()
