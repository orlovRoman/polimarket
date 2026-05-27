import json
import logging
from agents.shared.python.db import get_connection, cleanup_stale_signals, save_agent_episode
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
              AND m.close_time < datetime('now', '+15 minutes')
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
                
                try:
                    details_data = json.loads(row['details'])
                    if 'target_outcome' in details_data:
                        target = details_data['target_outcome'].upper()
                    
                    agent_name = details_data.get('agent_name', 'SCOUT')
                    predicted_prob = details_data.get('estimated_probability', 0.5)
                except (json.JSONDecodeError, TypeError, KeyError):
                    agent_name = 'SCOUT'
                    predicted_prob = 0.5
                
                is_win = False
                if target == 'YES' and winner_index == 0:
                    is_win = True
                elif target == 'NO' and winner_index == 1:
                    is_win = True
                    
                new_status = 'WIN' if is_win else 'LOSS'
                
                cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (new_status, sig_id))
                count_resolved += 1
                logger.info(f"Signal {sig_id} resolved as {new_status} (Market: {row['title']})")
                
                resolved_outcome = 'YES' if winner_index == 0 else 'NO'
                outcome_label = 'correct' if is_win else 'incorrect'
                save_agent_episode(
                    agent_name=agent_name,
                    event_type='signal_resolved',
                    summary=f"Рынок '{row['title'][:50]}...' закрылся как {resolved_outcome}. Прогноз агента: {predicted_prob:.0%}. Результат: {new_status}",
                    market_id=m_id,
                    market_title=row['title'],
                    context=json.dumps({
                        'predicted_prob': predicted_prob,
                        'target': target,
                        'winner_index': winner_index,
                        'resolved_as': resolved_outcome
                    }),
                    outcome=outcome_label
                )
                
                # Обновляем накопленную точность в memory
                from agents.shared.python.db import get_memory, save_memory
                total_correct_key = f"{agent_name.lower()}_correct_total"
                total_eval_key   = f"{agent_name.lower()}_evaluated_total"
                accuracy_key     = f"{agent_name.lower()}_accuracy_pct"

                prev_correct  = get_memory(total_correct_key) or 0
                prev_total    = get_memory(total_eval_key)    or 0
                new_correct   = prev_correct + (1 if is_win else 0)
                new_total     = prev_total + 1
                new_accuracy  = round(new_correct / new_total * 100, 1) if new_total > 0 else 0.0

                save_memory(total_correct_key, new_correct, category='fact', priority=7)
                save_memory(total_eval_key,    new_total,   category='fact', priority=7)
                save_memory(accuracy_key,      new_accuracy, category='fact', priority=9)

                
            except Exception as e:
                logger.error(f"Error resolving {sig_id}: {e}")
                
        conn.commit()
    return count_resolved
