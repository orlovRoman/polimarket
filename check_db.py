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
    uniq_wallets = conn.execute("SELECT COUNT(DISTINCT wallet_address) FROM whale_portfolio_snapshots").fetchone()[0]
    total_wallets = conn.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
    print(f"Unique wallets in snapshots: {uniq_wallets}")
    print(f"Total wallets in wallets table: {total_wallets}")
    
    # Check if there are snapshots for wallets that are no longer in wallets table
    orphans = conn.execute("SELECT COUNT(DISTINCT wallet_address) FROM whale_portfolio_snapshots WHERE wallet_address NOT IN (SELECT address FROM wallets)").fetchone()[0]
    print(f"Orphaned wallets in snapshots: {orphans}")
    
except Exception as e:
    print("Error:", e)
