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
    uniq_markets = conn.execute("SELECT COUNT(DISTINCT market_id) FROM whale_portfolio_snapshots").fetchone()[0]
    null_close_time = conn.execute("SELECT COUNT(*) FROM whale_portfolio_snapshots WHERE market_close_time IS NULL").fetchone()[0]
    not_null_close_time = conn.execute("SELECT COUNT(*) FROM whale_portfolio_snapshots WHERE market_close_time IS NOT NULL").fetchone()[0]
    print(f"Unique markets: {uniq_markets}")
    print(f"Rows with close_time IS NULL: {null_close_time}")
    print(f"Rows with close_time IS NOT NULL: {not_null_close_time}")
    
except Exception as e:
    print("Error:", e)
