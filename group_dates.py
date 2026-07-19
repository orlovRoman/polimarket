import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    rows = conn.execute("SELECT synced_at, COUNT(*) FROM whale_portfolio_snapshots GROUP BY synced_at").fetchall()
    print("COUNTS:")
    for r in rows:
        print(f"Date: {r[0]}, Count: {r[1]}")
except Exception as e:
    print("ERROR:", e)
