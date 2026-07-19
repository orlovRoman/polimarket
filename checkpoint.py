import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    print("CHECKPOINTING...")
    res = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print("RESULT:", dict(res) if res else res)
    
    count = conn.execute('SELECT COUNT(*) FROM whale_portfolio_snapshots').fetchone()[0]
    print("NEW COUNT:", count)
    
    rows = conn.execute("SELECT DISTINCT synced_at FROM whale_portfolio_snapshots").fetchall()
    print("DATES AFTER CHECKPOINT:")
    for r in rows:
        print(r[0])
        
except Exception as e:
    print("ERROR:", e)
