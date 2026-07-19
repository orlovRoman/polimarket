import sqlite3
db = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
print("db settings:", db.execute('SELECT * FROM whale_settings').fetchall())
db.close()
