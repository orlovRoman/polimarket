import sqlite3

def rename_columns():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE penny_virtual_trades_history RENAME COLUMN pnl_cents TO pnl_points")
        print("Renamed pnl_cents to pnl_points in penny_virtual_trades_history")
    except Exception as e:
        print("Error on penny table:", e)
        
    try:
        c.execute("ALTER TABLE whale_virtual_trades_history RENAME COLUMN pnl_cents TO pnl_points")
        print("Renamed pnl_cents to pnl_points in whale_virtual_trades_history")
    except Exception as e:
        print("Error on whale table:", e)
        
    conn.commit()

if __name__ == '__main__':
    rename_columns()
