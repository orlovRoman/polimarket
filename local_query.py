import sqlite3

try:
    conn = sqlite3.connect('Z:/polymarket-bot/vault/database.sqlite')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.status, s.market_id, m.title, s.pnl_realized, s.resolved_at, s.target_outcome 
        FROM signals s 
        JOIN markets m ON s.market_id = m.id 
        WHERE m.title LIKE '%Syria%'
    """)
    rows = cursor.fetchall()
    for row in rows:
        print(row)
except Exception as e:
    print(f"Error: {e}")
