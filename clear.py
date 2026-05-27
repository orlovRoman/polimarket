import sqlite3
import os

db_path = os.path.join("vault", "database.sqlite")
conn = sqlite3.connect(db_path)
conn.execute("DELETE FROM memory WHERE key LIKE 'market_cooldown_%'")
conn.commit()
conn.close()
print("Cooldowns cleared!")
