from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, init_db
import sqlite3

def test_fetch_and_save():
    init_db()
    adapter = PolymarketAdapter()
    print("Fetching markets...")
    markets = adapter.list_markets(limit=5)
    print(f"Found {len(markets)} markets.")
    
    for m in markets:
        print(f"Saving market: {m.title} ({m.price})")
        save_market(m)
    
    print("Done. Checking DB...")
    from agents.shared.python.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM markets")
    count = cursor.fetchone()[0]
    print(f"Total markets in DB: {count}")
    conn.close()

if __name__ == "__main__":
    test_fetch_and_save()
