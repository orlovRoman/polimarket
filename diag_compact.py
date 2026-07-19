import sqlite3, sys

db = sqlite3.connect('file:///home/orlovrp/polymarket-bot/vault/database.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row

queries = [
    ("total_tx", "SELECT count(*) as c FROM trader_transactions"),
    ("tx_2h", "SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours')"),
    ("tx_24h", "SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-24 hours')"),
    ("big_2h", "SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours') AND amount_usd > 1000"),
    ("wallets", "SELECT count(*) as c FROM wallets"),
    ("wallets_wr_pos", "SELECT count(*) as c FROM wallets WHERE win_rate > 0"),
    ("wallets_qual", "SELECT count(*) as c FROM wallets WHERE win_rate >= 0.5 AND n_trades >= 10"),
    ("insiders", "SELECT count(*) as c FROM wallets WHERE is_insider = 1"),
    ("tx_0x", "SELECT count(*) as c FROM trader_transactions WHERE market_id LIKE '0x%'"),
    ("tx_numeric", "SELECT count(*) as c FROM trader_transactions WHERE market_id NOT LIKE '0x%'"),
    ("now", "SELECT datetime('now') as c"),
]

for name, q in queries:
    try:
        val = db.execute(q).fetchone()['c']
        print(f"{name}={val}")
    except Exception as e:
        print(f"{name}=ERROR:{e}")

r = db.execute("SELECT timestamp, amount_usd FROM trader_transactions ORDER BY timestamp DESC LIMIT 1").fetchone()
print(f"last_tx_time={r['timestamp'] if r else 'NONE'}")
print(f"last_tx_amt={r['amount_usd'] if r else 'NONE'}")

try:
    for r in db.execute("SELECT key, value FROM whale_settings").fetchall():
        print(f"ws_{r['key']}={r['value']}")
except:
    print("ws_TABLE=NOT_FOUND")

try:
    alerts = db.execute("SELECT count(*) as c FROM sent_alerts WHERE created_at > datetime('now', '-2 hours')").fetchone()['c']
    print(f"alerts_suppressed_2h={alerts}")
except:
    print("alerts_TABLE=NOT_FOUND")

# JOIN analysis
join_ok = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours')").fetchone()['c']
print(f"tx_2h_join_ok={join_ok}")
no_join = db.execute("SELECT count(*) as c FROM trader_transactions t LEFT JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours') AND m.id IS NULL").fetchone()['c']
print(f"tx_2h_no_join={no_join}")

# Last 5 big trades
for r in db.execute("""
    SELECT t.timestamp ts, t.wallet_address wa, t.amount_usd amt, t.outcome oc,
           w.win_rate wr, w.n_trades nt, w.is_insider ins,
           m.title tt, m.volume vol
    FROM trader_transactions t
    LEFT JOIN markets m ON t.market_id = m.id
    LEFT JOIN wallets w ON t.wallet_address = w.address
    WHERE t.amount_usd > 1000
    ORDER BY t.timestamp DESC LIMIT 5
""").fetchall():
    wr = r['wr'] if r['wr'] else 0
    nt = r['nt'] if r['nt'] else 0
    ins = r['ins'] if r['ins'] else 0
    vol = r['vol'] if r['vol'] else 0
    tt = (r['tt'] or 'N/A')[:45]
    print(f"BIG|{r['ts']}|${r['amt']:,.0f}|{r['oc']}|wr={wr:.2f}|nt={nt}|ins={ins}|vol={vol}|{tt}")

# Day distribution
for r in db.execute("""
    SELECT date(timestamp) d, count(*) cnt FROM trader_transactions
    WHERE timestamp > datetime('now', '-7 days') GROUP BY date(timestamp) ORDER BY d DESC
""").fetchall():
    print(f"DAY|{r['d']}|{r['cnt']}")

db.close()
