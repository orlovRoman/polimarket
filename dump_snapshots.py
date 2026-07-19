import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM whale_portfolio_snapshots LIMIT 5').fetchall()
    for r in rows:
        print(dict(r))
except Exception as e:
    print("ERROR:", e)
