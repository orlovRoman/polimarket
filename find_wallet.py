import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    r = conn.execute("SELECT address, alias FROM wallets WHERE address LIKE '0xc84f7e%'").fetchall()
    print("WALLETS MATCHING:")
    for w in r:
        print(dict(w) if isinstance(w, sqlite3.Row) else w)
except Exception as e:
    print("ERROR:", e)
