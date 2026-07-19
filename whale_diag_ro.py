import sqlite3

db = sqlite3.connect('file:///home/orlovrp/polymarket-bot/vault/database.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row

print("="*60)
print("WHALE FOLLOWING DIAGNOSTICS")
print("="*60)

total = db.execute("SELECT count(*) as c FROM trader_transactions").fetchone()['c']
print(f"total_tx={total}")

recent_2h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours')").fetchone()['c']
print(f"tx_2h={recent_2h}")

recent_4h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-4 hours')").fetchone()['c']
print(f"tx_4h={recent_4h}")

recent_24h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-24 hours')").fetchone()['c']
print(f"tx_24h={recent_24h}")

last_tx = db.execute("SELECT timestamp, wallet_address, market_id, amount_usd FROM trader_transactions ORDER BY timestamp DESC LIMIT 1").fetchone()
if last_tx:
    print(f"last_tx_time={last_tx['timestamp']}")
    print(f"last_tx_amount=${last_tx['amount_usd']}")
else:
    print("NO_TRANSACTIONS")

big_2h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours') AND amount_usd > 1000").fetchone()['c']
print(f"big_tx_2h={big_2h}")

wallets_total = db.execute("SELECT count(*) as c FROM wallets").fetchone()['c']
wallets_with_wr = db.execute("SELECT count(*) as c FROM wallets WHERE win_rate IS NOT NULL AND win_rate > 0").fetchone()['c']
wallets_qualified = db.execute("SELECT count(*) as c FROM wallets WHERE win_rate >= 0.5 AND n_trades >= 10").fetchone()['c']
insiders = db.execute("SELECT count(*) as c FROM wallets WHERE is_insider = 1").fetchone()['c']
print(f"wallets_total={wallets_total}")
print(f"wallets_with_wr={wallets_with_wr}")
print(f"wallets_qualified_05_10={wallets_qualified}")
print(f"wallets_insider={insiders}")

try:
    rows = db.execute("SELECT key, value FROM whale_settings").fetchall()
    for r in rows:
        print(f"setting_{r['key']}={r['value']}")
except:
    print("NO_WHALE_SETTINGS_TABLE")

try:
    alerts = db.execute("SELECT count(*) as c FROM sent_alerts WHERE created_at > datetime('now', '-2 hours')").fetchone()['c']
    print(f"suppressed_alerts_2h={alerts}")
except:
    print("NO_SENT_ALERTS_TABLE")

join_check = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours')").fetchone()['c']
print(f"tx_2h_with_market_join={join_check}")

no_join = db.execute("SELECT count(*) as c FROM trader_transactions t LEFT JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours') AND m.id IS NULL").fetchone()['c']
print(f"tx_2h_no_market={no_join}")

full_scan = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id LEFT JOIN wallets w ON t.wallet_address = w.address WHERE t.timestamp > datetime('now', '-2 hours') AND t.amount_usd > 1000.0").fetchone()['c']
print(f"big_tx_2h_with_joins={full_scan}")

qualified = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id LEFT JOIN wallets w ON t.wallet_address = w.address WHERE t.timestamp > datetime('now', '-2 hours') AND t.amount_usd > 1000.0 AND (w.is_insider = 1 OR (w.win_rate >= 0.5 AND w.n_trades >= 10))").fetchone()['c']
print(f"big_tx_qualified_wallet={qualified}")

tx_0x = db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id LIKE '0x%'").fetchone()['c']
tx_numeric = db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id NOT LIKE '0x%'").fetchone()['c']
print(f"tx_with_0x_id={tx_0x}")
print(f"tx_with_numeric_id={tx_numeric}")

server_time = db.execute("SELECT datetime('now') as t").fetchone()['t']
print(f"sqlite_now={server_time}")

rows = db.execute("""
    SELECT t.timestamp, t.wallet_address, t.market_id, t.amount_usd, t.outcome, t.price,
           w.win_rate, w.n_trades, w.is_insider,
           m.title, m.price as market_price, m.volume
    FROM trader_transactions t
    LEFT JOIN markets m ON t.market_id = m.id
    LEFT JOIN wallets w ON t.wallet_address = w.address
    WHERE t.amount_usd > 1000
    ORDER BY t.timestamp DESC
    LIMIT 5
""").fetchall()
print("---LAST_5_BIG_TRADES---")
for r in rows:
    wr = r['win_rate'] if r['win_rate'] else 0
    nt = r['n_trades'] if r['n_trades'] else 0
    ins = r['is_insider'] if r['is_insider'] else 0
    vol = r['volume'] if r['volume'] else 0
    print(f"{r['timestamp']}|${r['amount_usd']:,.0f}|{r['outcome']}|wr={wr:.2f}|nt={nt}|ins={ins}|vol={vol}|{(r['title'] or 'N/A')[:50]}")

ws = db.execute("""
    SELECT
        count(DISTINCT t.wallet_address) as total_wallets,
        count(DISTINCT CASE WHEN w.address IS NOT NULL THEN t.wallet_address END) as in_wallets_table,
        count(DISTINCT CASE WHEN w.win_rate IS NOT NULL AND w.win_rate > 0 THEN t.wallet_address END) as with_wr,
        count(DISTINCT CASE WHEN w.n_trades >= 10 THEN t.wallet_address END) as with_enough_trades
    FROM trader_transactions t
    LEFT JOIN wallets w ON t.wallet_address = w.address
    WHERE t.timestamp > datetime('now', '-2 hours')
    AND t.amount_usd > 1000
""").fetchone()
print(f"recent_big_wallets_total={ws['total_wallets']}")
print(f"recent_big_wallets_in_table={ws['in_wallets_table']}")
print(f"recent_big_wallets_with_wr={ws['with_wr']}")
print(f"recent_big_wallets_enough_trades={ws['with_enough_trades']}")

db.close()
