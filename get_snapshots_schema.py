import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    res = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='whale_portfolio_snapshots'").fetchone()
    print("SNAPSHOTS SCHEMA:", res[0] if res else "Not found")
except Exception as e:
    print("ERROR:", e)
