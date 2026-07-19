import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c1 = conn.execute("SELECT COUNT(*) FROM wallets WHERE address LIKE '%b6dcf38f828a2c0612acfe2a2d2449f1'").fetchone()[0]
    c2 = conn.execute("SELECT COUNT(*) FROM wallets WHERE address LIKE '%3f9e9cf2ef0df3b1e84a2f8b1accff5f4bb5be'").fetchone()[0]
    print("SUFFIX 1 COUNT:", c1)
    print("SUFFIX 2 COUNT:", c2)
except Exception as e:
    print("ERROR:", e)
