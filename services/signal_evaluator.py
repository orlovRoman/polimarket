# services/signal_evaluator.py
"""
Сервис автоматической оценки точности сигналов после закрытия рынков.
Запускается по расписанию раз в 6 часов из main.py.
"""
from config import logger
from agents.shared.python.db import (
    get_connection, save_agent_episode, get_memory, save_memory
)
from agents.shared.adapters.polymarket import PolymarketAdapter


def evaluate_closed_signals() -> dict:
    """
    Находит сигналы по закрытым рынкам, сравнивает направление
    с реальным исходом и обновляет episodic memory агентов.

    Возвращает: {'evaluated': N, 'correct': N, 'incorrect': N}
    """
    stats = {"evaluated": 0, "correct": 0, "incorrect": 0}
    adapter = PolymarketAdapter()

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, s.market_id, s.type, s.edge, s.confidence, s.details,
                       m.title, m.close_time, m.price as last_known_price
                FROM signals s
                JOIN markets m ON s.market_id = m.id
                WHERE s.status = 'PENDING'
                  AND m.close_time < datetime('now')
                LIMIT 20
            """)
            signals = [dict(r) for r in cursor.fetchall()]

        for sig in signals:
            try:
                market = adapter.get_market(sig['market_id'])
                if not market:
                    continue

                # Рынок закрыт если цена = 1.0 (YES выиграл) или 0.0 (NO выиграл)
                resolved_yes = market.price >= 0.95
                resolved_no = market.price <= 0.05

                if not (resolved_yes or resolved_no):
                    continue  # Ещё не разрешён

                signal_type = sig.get('type', 'MISPRICING')
                
                import json as _json
                target = 'YES'
                try:
                    # Пробуем достать target_outcome из details сигнала
                    details_data = _json.loads(sig.get('details') or '{}')
                    target = details_data.get('target_outcome', 'YES').upper()
                except (ValueError, TypeError, KeyError):
                    pass
                
                if target == 'YES':
                    correct = resolved_yes
                elif target == 'NO':
                    correct = resolved_no
                else:
                    correct = resolved_yes  # fallback

                outcome = 'correct' if correct else 'incorrect'
                stats["evaluated"] += 1
                stats["correct" if correct else "incorrect"] += 1

                # Сохраняем эпизод оценки
                save_agent_episode(
                    agent_name="SCOUT",
                    event_type="signal_evaluated",
                    market_id=sig['market_id'],
                    market_title=sig['title'],
                    summary=f"Сигнал {signal_type} оценён как {outcome}. Edge был {sig['edge']:.2f}",
                    context={"edge": sig['edge'], "confidence": sig['confidence'],
                             "final_price": market.price},
                    outcome=outcome
                )

                # Архивируем оценённый сигнал
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE signals SET status = 'EVALUATED' WHERE id = ?",
                        (sig['id'],)
                    )

            except Exception as e:
                logger.error(f"[Evaluator] Ошибка оценки сигнала {sig.get('id')}: {e}")
                continue

        # Обновляем summary точности в memory
        if stats["evaluated"] > 0:
            total_correct = (get_memory("scout_correct_total") or 0) + stats["correct"]
            total_signals = (get_memory("scout_evaluated_total") or 0) + stats["evaluated"]
            save_memory("scout_correct_total", total_correct, category='fact', priority=7)
            save_memory("scout_evaluated_total", total_signals, category='fact', priority=7)
            accuracy = round(total_correct / total_signals * 100, 1)
            save_memory("scout_accuracy_pct", accuracy, category='fact', priority=9)
            logger.info(f"[Evaluator] Оценено: {stats}. Накопленная точность SCOUT: {accuracy}%")

    except Exception as e:
        logger.error(f"[Evaluator] Критическая ошибка: {e}")

    return stats
