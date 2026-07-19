import sys
sys.path.insert(0, '/home/orlovrp/polymarket-bot')
import config
import sqlite3

print("DB_PATH:", config.DB_PATH)

try:
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Show settings
    print("=== current settings ===")
    for row in cursor.execute("SELECT key, value FROM whale_settings").fetchall():
        print(f"{row['key']}: {row['value']}")
        
    # Update max_market_price
    cursor.execute("UPDATE whale_settings SET value = '0.95' WHERE key = 'max_market_price'")
    db.commit()
    
    print("=== updated settings ===")
    for row in cursor.execute("SELECT key, value FROM whale_settings").fetchall():
        print(f"{row['key']}: {row['value']}")
        
    db.close()
except Exception as e:
    print("Error:", e)
