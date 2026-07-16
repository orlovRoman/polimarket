import sqlite3
import time

db_path = '/home/orlovrp/polymarket-bot/vault/database.sqlite'
print(f"Connecting to database: {db_path}...")
try:
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    print(f"Connected in {time.time() - t0:.3f}s. Running query...")
    t0 = time.time()
    count = conn.execute("SELECT COUNT(*) FROM whale_portfolio_snapshots").fetchone()[0]
    print(f"Counted {count} rows in {time.time() - t0:.3f}s.")
    
    t0 = time.time()
    print("Testing get_whale_radar_summary(1) execution...")
    # Simulate get_whale_radar_summary logic
    rows = conn.execute("""
        SELECT
            w.market_id,
            w.market_title,
            w.market_url,
            w.market_close_time,
            w.outcome,
            COUNT(DISTINCT w.wallet_address)  AS whale_count,
            SUM(w.current_value)              AS total_usd
        FROM whale_portfolio_snapshots w
        WHERE w.market_close_time IS NULL
           OR w.market_close_time > datetime('now', '-1 day')
        GROUP BY w.market_id, w.outcome
        ORDER BY total_usd DESC
    """).fetchall()
    print(f"Query returned {len(rows)} rows in {time.time() - t0:.3f}s.")
except Exception as e:
    print("Error:", e)
