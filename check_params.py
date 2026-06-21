import sqlite3
conn = sqlite3.connect('c:/Users/orlov/.gemini/antigravity-ide/scratch/polimarket/vault/database.sqlite')
statuses=['approved', 'rejected']
placeholders = ','.join('?' for _ in statuses)
print(conn.execute(f'SELECT * FROM calibration_params WHERE status IN ({placeholders}) LIMIT ?', tuple(statuses) + (20,)).fetchall())
