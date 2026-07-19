import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT address, alias FROM wallets LIMIT 20").fetchall()
    print("WALLETS ADDRESSES:")
    for r in rows:
        print(dict(r))
except Exception as e:
    print("ERROR:", e)
