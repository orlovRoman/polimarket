import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c_active = conn.execute("SELECT COUNT(*) FROM markets WHERE close_time > datetime('now', '-1 day')").fetchone()[0]
    c_all = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    print("TOTAL MARKETS:", c_all)
    print("ACTIVE MARKETS:", c_active)
    
    # Print examples of active markets
    rows = conn.execute("SELECT id, title, close_time FROM markets WHERE close_time > datetime('now', '-1 day') LIMIT 10").fetchall()
    print("ACTIVE MARKETS EXAMPLES:")
    for r in rows:
        print(dict(r))
except Exception as e:
    print("ERROR:", e)
