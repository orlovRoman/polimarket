import sqlite3
from datetime import datetime, timezone
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    conn.row_factory = sqlite3.Row
    
    # 1. Signals count and latest signal
    sig_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    latest_sig = conn.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT 1").fetchone()
    print("SIGNALS COUNT:", sig_count)
    if latest_sig:
        print("LATEST SIGNAL:", dict(latest_sig))
        
    # 2. Trader transactions count and latest transaction
    tx_count = conn.execute("SELECT COUNT(*) FROM trader_transactions").fetchone()[0]
    latest_tx = conn.execute("SELECT * FROM trader_transactions ORDER BY timestamp DESC LIMIT 1").fetchone()
    print("TX COUNT:", tx_count)
    if latest_tx:
        print("LATEST TX:", dict(latest_tx))
        
    # 3. Whale portfolio snapshots count and distinct synced_at
    snapshots_count = conn.execute("SELECT COUNT(*) FROM whale_portfolio_snapshots").fetchone()[0]
    print("SNAPSHOTS COUNT:", snapshots_count)
    distinct_synced_at = conn.execute("SELECT DISTINCT synced_at FROM whale_portfolio_snapshots").fetchall()
    print("DISTINCT SYNCED_AT:")
    for row in distinct_synced_at:
        print(row[0])
        
except Exception as e:
    print("ERROR:", e)
