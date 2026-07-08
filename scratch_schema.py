import sqlite3
import json

def check_schema():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c = conn.cursor()
    
    c.execute("PRAGMA table_info(penny_virtual_trades_history)")
    print("penny:", json.dumps(c.fetchall(), indent=2))
    
    c.execute("PRAGMA table_info(whale_virtual_trades_history)")
    print("whale:", json.dumps(c.fetchall(), indent=2))

if __name__ == '__main__':
    check_schema()
