import sqlite3
try:
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    conn.row_factory = sqlite3.Row
    distinct_wallets = conn.execute("SELECT wallet_address, COUNT(*) as cnt, MIN(synced_at), MAX(synced_at) FROM whale_portfolio_snapshots GROUP BY wallet_address").fetchall()
    print("DISTINCT WALLETS:")
    for w in distinct_wallets:
        print(dict(w))
except Exception as e:
    print("ERROR:", e)
