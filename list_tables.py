import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print("TABLES:")
    for t in tables:
        name = t[0]
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"Table: {name}, Rows: {count}")
        except Exception as e:
            print(f"Table: {name}, Error: {e}")
except Exception as e:
    print("ERROR:", e)
