import sqlite3
import sys

conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
cursor = conn.cursor()
cursor.execute("SELECT id, title, price, outcome, close_time FROM markets WHERE title LIKE '%France win the 2026 FIFA World Cup%'")
markets = cursor.fetchall()
print("MARKETS:")
for m in markets:
    print(m)
    
cursor.execute("SELECT id, status, market_id, target_outcome, pnl_realized, resolved_at, resolution_outcome FROM signals WHERE market_id IN (SELECT id FROM markets WHERE title LIKE '%France win the 2026 FIFA World Cup%')")
signals = cursor.fetchall()
print("SIGNALS:")
for s in signals:
    print(s)
    
cursor.execute("SELECT * FROM agent_episodes WHERE market_id IN (SELECT id FROM markets WHERE title LIKE '%France win the 2026 FIFA World Cup%') AND event_type = 'signal_resolved'")
episodes = cursor.fetchall()
print("EPISODES:")
for e in episodes:
    print(e)
