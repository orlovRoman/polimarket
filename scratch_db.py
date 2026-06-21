import sqlite3
import sys

try:
    conn = sqlite3.connect('c:/Users/orlov/.gemini/antigravity-ide/scratch/polimarket/vault/database.sqlite')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    period_start = '2023-01-01 00:00:00'
    virtual_stake = 10.0
    
    # Test new equity curve query
    cursor.execute("""
                SELECT date(ts) as date, SUM(pnl) as daily_pnl
                FROM (
                    SELECT sold_at as ts, 
                           (bet_size_usdc * pnl_percent / 100.0) as pnl
                    FROM whale_virtual_trades_history
                    WHERE sold_at >= ? AND sold_at IS NOT NULL AND bought_outcome_price > 0
                    
                    UNION ALL
                    
                    SELECT resolved_at as ts,
                           (CASE 
                               WHEN UPPER(predicted_outcome) = 'YES' THEN 
                                   (CASE WHEN UPPER(actual_outcome) = 'YES' THEN 1.0 ELSE 0.0 END) - initial_price
                               WHEN UPPER(predicted_outcome) = 'NO' THEN 
                                   (CASE WHEN UPPER(actual_outcome) = 'NO' THEN 1.0 ELSE 0.0 END) - (1.0 - initial_price)
                               ELSE 0.0
                           END) * (? / NULLIF(CASE 
                               WHEN UPPER(predicted_outcome) = 'NO' THEN 1.0 - initial_price 
                               ELSE initial_price 
                           END, 0)) as pnl
                    FROM whale_stocks_monitoring
                    WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL AND resolved_at >= ?
                      AND (CASE WHEN UPPER(predicted_outcome) = 'NO' THEN 1.0 - initial_price ELSE initial_price END) > 0
                      AND market_id NOT IN (
                          SELECT DISTINCT market_id FROM whale_virtual_trades_history
                      )
                )
                GROUP BY date(ts)
                ORDER BY date(ts) ASC
    """, (period_start, virtual_stake, period_start))
    rows = cursor.fetchall()
    print("New Equity curve:")
    for row in rows:
        print(dict(row))

except Exception as e:
    print(f"Error: {e}")
