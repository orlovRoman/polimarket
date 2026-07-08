import sqlite3

def repair():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    cursor = conn.cursor()
    
    # Сбросить сигнал по Сирии
    cursor.execute("""
        UPDATE signals 
        SET status = 'PENDING', resolved_at = NULL, pnl_realized = NULL, resolution_outcome = NULL, was_profitable = NULL 
        WHERE id = 'manual_897227_1780831445'
    """)
    
    # Также сбросить статус рынка, если он был ошибочно разрешен
    cursor.execute("""
        UPDATE markets 
        SET outcome = 'unknown' 
        WHERE id = '897227'
    """)
    
    # Найти и удалить ошибочный эпизод
    cursor.execute("""
        DELETE FROM agent_episodes 
        WHERE market_id = '897227' AND event_type = 'signal_resolved'
    """)
    
    conn.commit()
    print("Repaired Trump Syria signal.")
    
if __name__ == "__main__":
    repair()
