import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c_wallets = conn.execute("SELECT COUNT(DISTINCT wallet_address) FROM whale_portfolio_snapshots").fetchone()[0]
    print("DISTINCT WALLETS COUNT:", c_wallets)
except Exception as e:
    print("ERROR:", e)
