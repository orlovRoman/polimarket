import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    triggers = conn.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'").fetchall()
    print("TRIGGERS:")
    for t in triggers:
        print(dict(t) if isinstance(t, sqlite3.Row) else t)
except Exception as e:
    print("ERROR:", e)
