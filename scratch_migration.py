import sqlite3

def run_migration():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE trader_transactions ADD COLUMN tx_hash TEXT")
        print("Column tx_hash added.")
    except sqlite3.OperationalError as e:
        print(f"Skipping ADD COLUMN: {e}")
        
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_hash ON trader_transactions(tx_hash) WHERE tx_hash IS NOT NULL")
        print("Index idx_tx_hash created.")
    except Exception as e:
        print(f"Index error: {e}")
        
    conn.commit()
    conn.close()

if __name__ == '__main__':
    run_migration()
