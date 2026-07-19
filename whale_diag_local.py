import sqlite3

db = sqlite3.connect('file:///Z:/polymarket-bot/vault/database.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row

print("="*60)
print("WHALE FOLLOWING DIAGNOSTICS")
print("="*60)

total = db.execute("SELECT count(*) as c FROM trader_transactions").fetchone()['c']
print(f"\n1. Всего транзакций в trader_transactions: {total}")

recent_2h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours')").fetchone()['c']
print(f"2. Транзакций за последние 2 часа: {recent_2h}")

recent_4h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-4 hours')").fetchone()['c']
print(f"3. Транзакций за последние 4 часа: {recent_4h}")

recent_24h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-24 hours')").fetchone()['c']
print(f"4. Транзакций за последние 24 часа: {recent_24h}")

last_tx = db.execute("SELECT timestamp, wallet_address, market_id, amount_usd FROM trader_transactions ORDER BY timestamp DESC LIMIT 1").fetchone()
if last_tx:
    print(f"5. Последняя транзакция: {last_tx['timestamp']}, wallet={last_tx['wallet_address'][:12]}..., amount=${last_tx['amount_usd']}")
else:
    print("5. Транзакций НЕТ ВООБЩЕ!")

big_2h = db.execute("SELECT count(*) as c FROM trader_transactions WHERE timestamp > datetime('now', '-2 hours') AND amount_usd > 1000").fetchone()['c']
print(f"6. Крупных сделок (>$1000) за 2ч: {big_2h}")

wallets_total = db.execute("SELECT count(*) as c FROM wallets").fetchone()['c']
wallets_with_wr = db.execute("SELECT count(*) as c FROM wallets WHERE win_rate IS NOT NULL AND win_rate > 0").fetchone()['c']
wallets_qualified = db.execute("SELECT count(*) as c FROM wallets WHERE win_rate >= 0.5 AND n_trades >= 10").fetchone()['c']
insiders = db.execute("SELECT count(*) as c FROM wallets WHERE is_insider = 1").fetchone()['c']
print(f"\n7. Кошельков всего: {wallets_total}")
print(f"   С win_rate > 0: {wallets_with_wr}")
print(f"   Квалифицированных (wr>=0.5, trades>=10): {wallets_qualified}")
print(f"   Инсайдеров: {insiders}")

print(f"\n8. Настройки whale_settings:")
try:
    rows = db.execute("SELECT key, value FROM whale_settings").fetchall()
    for r in rows:
        print(f"   {r['key']} = {r['value']}")
except:
    print("   Таблица whale_settings не найдена")

try:
    alerts = db.execute("SELECT count(*) as c FROM sent_alerts WHERE created_at > datetime('now', '-2 hours')").fetchone()['c']
    print(f"\n9. Подавленных алертов за 2ч: {alerts}")
except:
    print("\n9. Таблица sent_alerts не найдена")

join_check = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours')").fetchone()['c']
print(f"10. Транзакций за 2ч, JOIN-ятся с markets: {join_check}")

no_join = db.execute("SELECT count(*) as c FROM trader_transactions t LEFT JOIN markets m ON t.market_id = m.id WHERE t.timestamp > datetime('now', '-2 hours') AND m.id IS NULL").fetchone()['c']
print(f"11. Транзакций за 2ч БЕЗ market: {no_join}")

full_scan = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id LEFT JOIN wallets w ON t.wallet_address = w.address WHERE t.timestamp > datetime('now', '-2 hours') AND t.amount_usd > 1000.0").fetchone()['c']
print(f"12. Крупных сделок с JOIN markets+wallets: {full_scan}")

qualified = db.execute("SELECT count(*) as c FROM trader_transactions t JOIN markets m ON t.market_id = m.id LEFT JOIN wallets w ON t.wallet_address = w.address WHERE t.timestamp > datetime('now', '-2 hours') AND t.amount_usd > 1000.0 AND (w.is_insider = 1 OR (w.win_rate >= 0.5 AND w.n_trades >= 10))").fetchone()['c']
print(f"13. С квалифицированным кошельком: {qualified}")

tx_0x = db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id LIKE '0x%'").fetchone()['c']
tx_numeric = db.execute("SELECT count(*) as c FROM trader_transactions WHERE market_id NOT LIKE '0x%'").fetchone()['c']
print(f"\n14. Транзакции с 0x market_id: {tx_0x}")
print(f"    Транзакции с числовым market_id: {tx_numeric}")

server_time = db.execute("SELECT datetime('now') as t").fetchone()['t']
print(f"\n15. Текущее время SQLite (UTC): {server_time}")

print(f"\n16. Последние 5 крупных сделок (>$1000):")
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
for r in rows:
    wr = r['win_rate'] if r['win_rate'] else 0
    nt = r['n_trades'] if r['n_trades'] else 0
    ins = r['is_insider'] if r['is_insider'] else 0
    vol = r['volume'] if r['volume'] else 0
    print(f"   {r['timestamp']} | ${r['amount_usd']:,.0f} | {r['outcome']} @ {r['price']} | wr={wr:.2f} nt={nt} ins={ins} | vol={vol} | {(r['title'] or 'N/A')[:50]}")

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
print(f"\n17. Кошельки из крупных сделок за 2ч:")
print(f"    Уникальных: {ws['total_wallets']}")
print(f"    Есть в wallets: {ws['in_wallets_table']}")
print(f"    С win_rate>0: {ws['with_wr']}")
print(f"    С n_trades>=10: {ws['with_enough_trades']}")

# Scan_volume_spikes simulation
print(f"\n18. Симуляция scan_volume_spikes (последние 4ч):")
spikes = db.execute("""
    SELECT t.market_id, m.title,
        SUM(CASE WHEN t.timestamp > datetime('now', '-2 hours') THEN t.amount_usd ELSE 0.0 END) AS vol_recent,
        SUM(CASE WHEN t.timestamp BETWEEN datetime('now', '-4 hours') AND datetime('now', '-2 hours') THEN t.amount_usd ELSE 0.0 END) AS vol_prev
    FROM trader_transactions t
    JOIN markets m ON t.market_id = m.id
    WHERE t.timestamp > datetime('now', '-4 hours')
    GROUP BY t.market_id
    HAVING vol_prev > 100.0 AND (vol_recent / vol_prev) >= 1.5
""").fetchall()
print(f"    Рынков с всплеском объема: {len(spikes)}")
for s in spikes[:3]:
    print(f"    {(s['title'] or 'N/A')[:40]} | recent=${s['vol_recent']:,.0f} prev=${s['vol_prev']:,.0f}")

# Check DataApiSyncer logs
print(f"\n19. Распределение транзакций по дням (последние 7 дней):")
day_dist = db.execute("""
    SELECT date(timestamp) as day, count(*) as cnt, SUM(amount_usd) as total_usd
    FROM trader_transactions
    WHERE timestamp > datetime('now', '-7 days')
    GROUP BY date(timestamp)
    ORDER BY day DESC
""").fetchall()
for d in day_dist:
    print(f"    {d['day']}: {d['cnt']} сделок, ${d['total_usd']:,.0f}")

db.close()
