import sqlite3

db = sqlite3.connect('file:///Z:/polymarket-bot/vault/database.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row

print("total_tx:", db.execute("SELECT count(*) as c FROM trader_transactions").fetchone()['c'])
print("tx_2h:", db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours')").fetchone()['c'])
print("tx_24h:", db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-24 hours')").fetchone()['c'])

r = db.execute("SELECT timestamp, amount_usd FROM trader_transactions ORDER BY timestamp DESC LIMIT 1").fetchone()
print("last_tx:", dict(r) if r else "NONE")

print("big_2h:", db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours') AND amount_usd > 1000").fetchone()['c'])

print("wallets:", db.execute("SELECT count(*) as c FROM wallets").fetchone()['c'])
print("wallets_wr>0:", db.execute("SELECT count(*) as c FROM wallets WHERE win_rate > 0").fetchone()['c'])
print("wallets_qualified:", db.execute("SELECT count(*) as c FROM wallets WHERE win_rate >= 0.5 AND n_trades >= 10").fetchone()['c'])
print("insiders:", db.execute("SELECT count(*) as c FROM wallets WHERE is_insider = 1").fetchone()['c'])

try:
    for r in db.execute("SELECT key, value FROM whale_settings").fetchall():
        print(f"setting: {r['key']} = {r['value']}")
except:
    print("NO whale_settings table")

print("tx_0x:", db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id LIKE '0x%'").fetchone()['c'])
print("tx_numeric:", db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id NOT LIKE '0x%'").fetchone()['c'])

print("sqlite_now:", db.execute("SELECT datetime('now') as t").fetchone()['t'])

db.close()
