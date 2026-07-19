import sqlite3

db = sqlite3.connect('file:///home/orlovrp/polymarket-bot/vault/database.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row

# What does the actual scan_large_single_bets return?
# Reproduce: tx > $1000 in 2h, JOIN markets, LEFT JOIN wallets
# Then filter: confidence >= 0.6 (wallet qualified)

rows = db.execute("""
    SELECT t.wallet_address, t.amount_usd, t.outcome, t.market_id,
           w.win_rate, w.n_trades, w.is_insider,
           m.volume, m.price as m_price, m.title
    FROM trader_transactions t
    JOIN markets m ON t.market_id = m.id
    LEFT JOIN wallets w ON t.wallet_address = w.address
    WHERE t.timestamp > datetime('now', '-2 hours')
    AND t.amount_usd > 1000.0
    ORDER BY t.amount_usd DESC
""").fetchall()

print(f"Total big trades (>$1000) in 2h: {len(rows)}")
print()

for r in rows:
    wr = r['win_rate'] or 0
    nt = r['n_trades'] or 0
    ins = r['is_insider'] or 0
    vol = r['volume'] or 0
    mp = r['m_price'] or 0
    
    # Reproduce confidence logic
    if ins:
        conf = 0.8
    elif wr >= 0.5 and nt >= 10:
        conf = 0.6
    else:
        conf = 0.3
    
    # Reproduce market filters (using DB settings)
    min_vol = 1000.0
    min_price = 0.01
    max_price = 0.8
    
    market_ok = vol >= min_vol and mp > min_price and mp < max_price
    wallet_ok = conf >= 0.6
    
    reason = "PASS" if market_ok and wallet_ok else ""
    if not market_ok:
        reasons = []
        if vol < min_vol:
            reasons.append(f"vol={vol}<{min_vol}")
        if mp <= min_price:
            reasons.append(f"price={mp}<=0.01")
        if mp >= max_price:
            reasons.append(f"price={mp}>=0.8")
        reason = "MARKET_FAIL:" + ",".join(reasons)
    elif not wallet_ok:
        reason = f"WALLET_FAIL:wr={wr:.2f},nt={nt},ins={ins}"
    
    print(f"${r['amount_usd']:>8,.0f} | {r['outcome']:>4} | wr={wr:.2f} nt={nt:>4} ins={ins} conf={conf} | vol={vol:>12} mp={mp} | {reason} | {(r['title'] or 'N/A')[:40]}")

# Check total wallets distribution for recent period
print("\n--- Wallet win_rate distribution (all wallets with n_trades >= 10) ---")
for r in db.execute("""
    SELECT 
        CASE 
            WHEN win_rate >= 0.7 THEN '0.70+'
            WHEN win_rate >= 0.6 THEN '0.60-0.69'
            WHEN win_rate >= 0.5 THEN '0.50-0.59'
            WHEN win_rate >= 0.4 THEN '0.40-0.49'
            WHEN win_rate > 0 THEN '0.01-0.39'
            ELSE '0.00'
        END as bucket,
        count(*) as cnt
    FROM wallets
    WHERE n_trades >= 10
    GROUP BY bucket
    ORDER BY bucket DESC
""").fetchall():
    print(f"  {r['bucket']}: {r['cnt']} wallets")

db.close()
