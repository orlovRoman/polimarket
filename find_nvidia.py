import sqlite3

current_db = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
current_db.row_factory = sqlite3.Row

m_cur = current_db.cursor()
m_cur.execute("SELECT id, title FROM markets WHERE title LIKE '%NVIDIA%'")
for m in m_cur.fetchall():
    print("Market:", dict(m))
    s_cur = current_db.cursor()
    s_cur.execute("SELECT id, strategy_type, target_outcome, confidence, market_price_at_signal, status FROM signals WHERE market_id=?", (m['id'],))
    for s in s_cur.fetchall():
        print("  Signal:", dict(s))
