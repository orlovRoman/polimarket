# services/signal_resolver.py
import logging
from datetime import datetime, timezone

logger = logging.getLogger("NexusPolyBot.SignalResolver")


def resolve_pending_signals(limit: int = 50) -> int:
    """
    Находит PENDING-сигналы с истёкшим рынком и резолвит их.
    
    Логика:
    - Рынок считается завершённым если close_time < now
    - resolution_outcome определяется по финальной цене:
      price >= 0.95 → YES победил, price <= 0.05 → NO победил, иначе → N/A
    - Сигнал был прибыльным если его target_outcome совпадает с resolution_outcome
    
    Возвращает количество разрешённых сигналов.
    """
    from agents.shared.python.db import get_connection
    from core.eval.signal_logger import SignalLogger

    # Находим PENDING-сигналы с истёкшим временем рынка
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT
                    s.id            AS signal_id,
                    s.market_id,
                    s.target_outcome,
                    s.estimated_probability,
                    s.edge,
                    m.price         AS final_price,
                    m.outcome       AS market_outcome,
                    m.close_time
                FROM signals s
                JOIN markets m ON s.market_id = m.id
                WHERE s.status = 'PENDING'
                  AND datetime(m.close_time) < datetime('now')
                  AND m.price IS NOT NULL
                LIMIT ?
            """, (limit,)).fetchall()
    except Exception as e:
        logger.error(f"[SignalResolver] Ошибка запроса: {e}", exc_info=True)
        return 0

    if not rows:
        logger.debug("[SignalResolver] Нет PENDING-сигналов для разрешения.")
        return 0

    eval_logger = SignalLogger()
    resolved_count = 0

    for row in rows:
        signal_id    = row["signal_id"]
        target       = (row["target_outcome"] or "YES").upper()
        final_price  = float(row["final_price"])
        market_outcome = (row["market_outcome"] or "").upper()

        # Определяем победивший исход по приоритету: сначала outcome из markets, затем по цене
        if market_outcome in ("YES", "NO"):
            resolution_outcome = market_outcome
            resolution_price   = 1.0 if market_outcome == "YES" else 0.0
        elif final_price >= 0.95:
            resolution_outcome = "YES"
            resolution_price   = 1.0
        elif final_price <= 0.05:
            resolution_outcome = "NO"
            resolution_price   = 0.0
        else:
            # Рынок ещё не разрешился (цена в середине) — пропускаем
            logger.debug(f"[SignalResolver] Сигнал {signal_id}: цена {final_price:.3f}, исход '{market_outcome}' — рынок ещё не разрешён, пропускаем.")
            continue

        try:
            eval_logger.log_resolution(
                signal_id=signal_id,
                resolution_outcome=resolution_outcome,
                resolution_price=resolution_price,
                resolved_at=datetime.now(timezone.utc)
            )
            resolved_count += 1
            won = "✅ WIN" if target == resolution_outcome else "❌ LOSS"
            logger.info(
                f"[SignalResolver] {signal_id[:8]}… | "
                f"target={target} outcome={resolution_outcome} price={final_price:.3f} → {won}"
            )
        except Exception as e:
            logger.error(f"[SignalResolver] Ошибка резолюции {signal_id}: {e}", exc_info=True)

    logger.info(f"[SignalResolver] Итого разрешено: {resolved_count} из {len(rows)} кандидатов")
    return resolved_count
