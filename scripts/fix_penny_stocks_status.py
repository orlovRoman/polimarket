import os
import sys
import json
import sqlite3
import requests
import time

# Добавляем корневую директорию проекта в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger

logger = setup_logger("PennyFix")

DB_PATH = "vault/database.sqlite"

def main():
    if not os.path.exists(DB_PATH):
        logger.error(f"Database not found at {DB_PATH}")
        return

    logger.info("Connecting to database...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Ищем все разрешенные Penny Stocks
    rows = cursor.execute("SELECT market_id, title FROM penny_stocks_monitoring WHERE status = 'RESOLVED'").fetchall()
    logger.info(f"Found {len(rows)} resolved penny stocks to check.")

    fixed_count = 0

    for row in rows:
        market_id = row['market_id']
        title = row['title']

        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch market {market_id}: HTTP {resp.status_code}")
                time.sleep(1)
                continue
                
            data = resp.json()
            closed = data.get('closed', False)

            if not closed:
                logger.info(f"Market {market_id} '{title[:50]}...' is actually ACTIVE on Polymarket. Reverting...")
                
                # Ищем запись в истории сделок, чтобы восстановить данные о покупке
                history_row = cursor.execute(
                    "SELECT bought_price, bought_at, bet_size_usdc FROM penny_virtual_trades_history WHERE market_id = ?", 
                    (market_id,)
                ).fetchone()

                v_bought = history_row['bought_price'] if history_row else None
                v_bought_at = history_row['bought_at'] if history_row else None
                v_bet_size = history_row['bet_size_usdc'] if history_row else None

                # Обновляем таблицу мониторинга
                cursor.execute("""
                    UPDATE penny_stocks_monitoring
                    SET status = 'ACTIVE',
                        actual_outcome = NULL,
                        resolved_at = NULL,
                        virtual_bought_price = ?,
                        virtual_bought_at = ?,
                        bet_size_usdc = ?
                    WHERE market_id = ?
                """, (v_bought, v_bought_at, v_bet_size, market_id))

                # Удаляем ложную запись из истории
                if history_row:
                    cursor.execute("DELETE FROM penny_virtual_trades_history WHERE market_id = ?", (market_id,))
                    logger.info(f"  Restored bought_price: {v_bought}, deleted from history.")
                else:
                    logger.info("  No trade history found.")

                conn.commit()
                fixed_count += 1
                
        except Exception as e:
            logger.error(f"Error processing market {market_id}: {e}")

        # Небольшая пауза, чтобы не спамить API
        time.sleep(0.5)

    logger.info(f"Check complete. Fixed {fixed_count} markets.")

if __name__ == "__main__":
    main()
