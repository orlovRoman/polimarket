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
    print("Creating index idx_wps_radar...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wps_radar ON whale_portfolio_snapshots(market_close_time, market_id, outcome, current_value, wallet_address)")
    print(f"Index created in {time.time() - t0:.3f}s.")
    
    t0 = time.time()
    print("Explaining get_whale_radar_summary query plan with new index...")
    plan = conn.execute("""
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
    for p in plan:
        print(dict(p))
        
    t0 = time.time()
    print("Running get_whale_radar_summary query...")
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
    print(f"Query completed in {time.time() - t0:.3f}s. Returned {len(rows)} rows.")
    
except Exception as e:
    print("Error:", e)
