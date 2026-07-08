import sqlite3

try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    cursor = conn.cursor()
    cursor.execute("SELECT summary, outcome, created_at FROM agent_episodes WHERE market_id = '897227'")
    rows = cursor.fetchall()
    for row in rows:
        print(row)
except Exception as e:
    print(f"Error: {e}")
