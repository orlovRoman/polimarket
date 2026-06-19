import sys
import os
import json

# Добавляем корневую директорию проекта в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.shared.python.db import get_connection

def _deduplicate_market(conn, r) -> bool:
    market_id = r['market_id']
    try:
        directions = json.loads(r['whale_directions'])
        wallet_map = {}
        for d in directions:
            w = d.get('wallet')
            if not w:
                continue
            if w not in wallet_map:
                wallet_map[w] = {'wallet': w, 'side': d.get('side', 'UNKNOWN'), 'amount_usd': d.get('amount_usd', 0)}
            else:
                wallet_map[w]['amount_usd'] += d.get('amount_usd', 0)
                wallet_map[w]['side'] = d.get('side', wallet_map[w]['side'])
        
        new_directions = list(wallet_map.values())
        new_count = len(new_directions)
        
        conn.execute("""
            UPDATE whale_stocks_monitoring
            SET whale_directions = ?, whale_count = ?
            WHERE market_id = ?
        """, (json.dumps(new_directions), new_count, market_id))
        return True
    except Exception as e:
        print(f"Ошибка при дедупликации {market_id}: {e}")
        return False

def migrate():
    print("Начинаем миграцию данных по китам...")
    
    with get_connection() as conn:
        # 1. Дедупликация whale_directions
        rows = conn.execute("SELECT market_id, whale_directions FROM whale_stocks_monitoring WHERE whale_directions IS NOT NULL").fetchall()
        updated_count = 0
        for r in rows:
            if _deduplicate_market(conn, r):
                updated_count += 1
        
        print(f"Дедуплицировано whale_directions для {updated_count} рынков.")
        
        # 2. Старые сделки (bet_size_usdc)
        c1 = conn.execute("""
            UPDATE whale_virtual_trades_history
            SET bet_size_usdc = 100.0
            WHERE bet_size_usdc IS NULL
        """)
        print(f"Обновлено {c1.rowcount} старых записей в истории сделок (bet_size_usdc=100.0).")
        
        c2 = conn.execute("""
            UPDATE whale_stocks_monitoring
            SET bet_size_usdc = 100.0
            WHERE bet_size_usdc IS NULL AND virtual_bought_price IS NOT NULL
        """)
        print(f"Обновлено {c2.rowcount} старых записей в активных портфелях (bet_size_usdc=100.0).")
        
        # 3. Перевод UNKNOWN в IGNORED
        c3 = conn.execute("""
            UPDATE whale_stocks_monitoring
            SET status = 'IGNORED'
            WHERE status = 'ACTIVE' AND predicted_outcome = 'UNKNOWN'
        """)
        print(f"Переведено в IGNORED {c3.rowcount} рынков с predicted_outcome='UNKNOWN'.")
        
        # 4. Добавление колонки resolved_at если её нет
        try:
            conn.execute("ALTER TABLE whale_stocks_monitoring ADD COLUMN resolved_at TIMESTAMP")
            print("Колонка resolved_at успешно добавлена в whale_stocks_monitoring.")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("Колонка resolved_at уже существует.")
            else:
                print(f"Ошибка при добавлении колонки resolved_at: {e}")

if __name__ == '__main__':
    migrate()
