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
    print("Table and indexes definition:")
    defs = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='whale_portfolio_snapshots'").fetchall()
    for d in defs:
        print(d['sql'])
    
except Exception as e:
    print("Error:", e)
