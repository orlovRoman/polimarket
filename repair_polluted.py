import sqlite3

def repair():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    cursor = conn.cursor()
    
    # Найти все рынки с outcome = 'Yes' или 'No'
    cursor.execute("SELECT id, title, outcome FROM markets WHERE outcome IN ('Yes', 'No')")
    polluted_markets = cursor.fetchall()
    
    print(f"Found {len(polluted_markets)} polluted markets.")
    for m in polluted_markets:
        print(f"Repairing market: {m[1]} (id={m[0]})")
        # 1. Сбросить рынок
        cursor.execute("UPDATE markets SET outcome = 'unknown' WHERE id = ?", (m[0],))
        
        # 2. Сбросить все сигналы по этому рынку, которые были ошибочно разрешены
        # Если сигнал был PENDING, а стал WIN/LOSS, мы возвращаем его в PENDING.
        cursor.execute("""
            UPDATE signals 
            SET status = 'PENDING', resolved_at = NULL, pnl_realized = NULL, resolution_outcome = NULL, was_profitable = NULL 
            WHERE market_id = ? AND (status = 'WIN' OR status = 'LOSS')
        """, (m[0],))
        
        # 3. Удалить эпизоды агентов
        cursor.execute("""
            DELETE FROM agent_episodes 
            WHERE market_id = ? AND event_type = 'signal_resolved'
        """, (m[0],))
        
    conn.commit()
    print("Repair complete.")
    
if __name__ == "__main__":
    repair()
