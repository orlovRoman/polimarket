import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    r = conn.execute("SELECT * FROM wallets WHERE address = '0xdummy123'").fetchone()
    print("DUMMY WALLET READ:", r)
except Exception as e:
    print("ERROR:", e)
