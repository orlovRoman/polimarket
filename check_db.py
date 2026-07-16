import sqlite3
import time

db_path = '/home/orlovrp/polymarket-bot/vault/database.sqlite'
print(f"Connecting to database: {db_path}...")
try:
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    print(f"Connected in {time.time() - t0:.3f}s. Checking journal mode...")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"Journal mode: {mode}")
    t0 = time.time()
    print("Running count query...")
    count = conn.execute("SELECT COUNT(*) FROM whale_portfolio_snapshots").fetchone()[0]
    print(f"Counted {count} rows in {time.time() - t0:.3f}s.")
    
    t0 = time.time()
    print("Explaining COUNT query plan...")
    plan1 = conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM whale_portfolio_snapshots").fetchall()
    for p in plan1:
        print(dict(p))
        
    print("Explaining get_whale_radar_summary query plan...")
    plan2 = conn.execute("""
        EXPLAIN QUERY PLAN
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
    for p in plan2:
        print(dict(p))
        
except Exception as e:
    print("Error:", e)
