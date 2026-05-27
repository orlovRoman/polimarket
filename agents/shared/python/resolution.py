import json
import logging
from agents.shared.python.db import get_connection, cleanup_stale_signals
from agents.shared.adapters.polymarket import PolymarketAdapter

logger = logging.getLogger("ResolutionCheck")

def resolve_closed_markets():
    """
    Проверяет статусы закрытых рынков через Polymarket API
    и проставляет WIN / LOSS для сигналов агентов.
    """
    # Сначала архивируем совсем старые и удаляем старше года (старая логика)
    try:
        cleanup_stale_signals()
    except Exception as e:
        logger.error(f"Error in cleanup_stale_signals: {e}")

    adapter = PolymarketAdapter()
    count_resolved = 0
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Берем сигналы в статусе ARCHIVED (которые мы только что туда перевели, 
        # потому что close_time < now). Либо PENDING, если вдруг пропустили.
        cursor.execute("""
            SELECT s.id as signal_id, s.market_id, m.title, s.status, s.details
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            WHERE s.status IN ('PENDING', 'ARCHIVED') 
              AND m.close_time < datetime('now', '+1 days')
              AND s.platform = 'polymarket'
        """)
        pending = cursor.fetchall()
        
        for row in pending:
            sig_id = row['signal_id']
            m_id = row['market_id']
            
            try:
                res = adapter.session.get(f"{adapter.api_url}/markets/{m_id}", timeout=10)
                if res.status_code != 200:
                    continue
                item = res.json()
                
                closed = item.get("closed", False)
                if not closed:
                    continue
                    
                outcome_prices = json.loads(item.get("outcomePrices", "[]"))
                if not outcome_prices:
                    continue
                
                try:
                    winner_index = outcome_prices.index("1")
                except ValueError:
                    # Если рынка закрыт, но победитель еще не определен
                    continue
                
                # Извлекаем target (по умолчанию считаем что YES = индекс 0)
                # Поскольку target_outcome не хранится прямо, а всегда подразумевается YES для первого токена в боте
                target = 'YES'
                
                # Если в details есть прямое указание
                try:
                    details = json.loads(row['details'])
                    if 'target_outcome' in details:
                        target = details['target_outcome'].upper()
                except:
                    pass
                
                is_win = False
                if target == 'YES' and winner_index == 0:
                    is_win = True
                elif target == 'NO' and winner_index == 1:
                    is_win = True
                    
                new_status = 'WIN' if is_win else 'LOSS'
                
                cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (new_status, sig_id))
                count_resolved += 1
                logger.info(f"Signal {sig_id} resolved as {new_status} (Market: {row['title']})")
                
            except Exception as e:
                logger.error(f"Error resolving {sig_id}: {e}")
                
        conn.commit()
    return count_resolved
