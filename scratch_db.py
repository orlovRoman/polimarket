import sqlite3

def check_db():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c = conn.cursor()
    c.execute("select count(*) from trader_transactions")
    total = c.fetchone()[0]
    c.execute("select count(*) from trader_transactions where timestamp >= datetime('now', '-1 hour')")
    recent = c.fetchone()[0]
    print(f"Total: {total}, Recent (1h): {recent}")
    
    # Check if there are any whales in wallets table
    c.execute("select count(*) from wallets")
    wallets = c.fetchone()[0]
    c.execute("select count(*) from wallets where win_rate > 0.6")
    good_wallets = c.fetchone()[0]
    print(f"Total wallets: {wallets}, Good wallets: {good_wallets}")

if __name__ == '__main__':
    check_db()
