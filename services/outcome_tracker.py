# services/outcome_tracker.py
"""
Outcome Tracker — авторезолюция сигналов по закрытым рынкам.
Запускается каждые 6 часов через scheduler.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.shared.python.db import get_connection

logger = logging.getLogger("NexusPolyBot.OutcomeTracker")

# ── Публичный API ───────────────────────────────────────────

def run_resolution_cycle() -> dict:
    """
    Главная точка входа. Находит все PENDING-сигналы с закрытым рынком,
    запрашивает resolution через Polymarket API, обновляет signals и strategy_metrics.
    Возвращает статистику прогона: {resolved, skipped, errors}.
    """
    try:
        from agents.shared.python.db import cleanup_stale_signals
        cleanup_stale_signals()
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка при очистке старых сигналов: {e}")

    stats = {"resolved": 0, "skipped": 0, "errors": 0}
    pending = _get_pending_with_closed_market()
    logger.info(f"[OutcomeTracker] Найдено {len(pending)} сигналов для резолюции.")

    if not pending:
        return stats

    resolved_items = []
    for row in pending:
        try:
            resolution = _fetch_resolution(row["market_id"])
            if resolution is None:
                try:
                    created_str = row["created_at"]
                    if " " in created_str and "+" not in created_str and "-" not in created_str[10:]:
                        created = datetime.fromisoformat(created_str).replace(tzinfo=timezone.utc)
                    else:
                        created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - created).days
                    if age_days >= 7:
                        _resolve_signal(row, "N/A")
                        stats["resolved"] += 1
                        resolved_items.append((row, "N/A"))
                    else:
                        stats["skipped"] += 1
                except Exception as parse_err:
                    logger.error(f"[OutcomeTracker] Ошибка разбора даты {row['created_at']}: {parse_err}")
                    stats["skipped"] += 1
                continue
            _resolve_signal(row, resolution)
            stats["resolved"] += 1
            resolved_items.append((row, resolution))
        except Exception as exc:
            logger.error(f"[OutcomeTracker] Ошибка резолюции {row['id']}: {exc}")
            stats["errors"] += 1

    # Авторезолюция Favourite Compounding позиций
    resolved_compounds = 0
    try:
        resolved_compounds = _resolve_compound_outcomes()
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка резолюции compound-позиций: {e}", exc_info=True)

    if stats["resolved"] > 0 or resolved_compounds > 0:
        _update_all_strategy_metrics()
        if stats["resolved"] > 0:
            _send_telegram_summary(resolved_items)

    logger.info(f"[OutcomeTracker] Итог: {stats}")
    return stats


# ── Внутренние функции ──────────────────────────────────────

def _get_pending_with_closed_market() -> list[dict]:
    """Возвращает PENDING-сигналы, готовые к резолюции (закрытые или старые для проверки по API)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id, s.market_id, s.target_outcome, s.strategy_type,
                   s.edge, s.confidence, s.created_at, s.estimated_probability,
                   s.predicted_probability, s.market_price_at_signal,
                   m.title as market_title, m.close_time, m.outcome as market_resolved_outcome
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            WHERE s.status = 'PENDING'
              AND (
                datetime(m.close_time) < datetime('now')
                OR m.outcome IN ('YES', 'NO')
                OR s.created_at < datetime('now', '-12 hours')
              )
              AND s.resolved_at IS NULL
        """).fetchall()
    return [dict(r) for r in rows]


def _fetch_resolution(market_id: str) -> Optional[str]:
    """
    Возвращает итоговый outcome рынка ('YES' / 'NO' / None если ещё неизвестен).
    Запрашиваем Polymarket API напрямую, так как локальная БД может содержать устаревшее значение.
    """
    # Live API check (импортируется лениво, чтобы тесты не зависели от сети)
    try:
        from services.polymarket_client import get_market_resolution
        res = get_market_resolution(market_id)
        if res in ("YES", "NO"):
            return res
    except Exception as exc:
        logger.warning(f"[OutcomeTracker] Live API недоступен для {market_id}: {exc}")

    # Fallback: пробуем взять из локальной БД
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT outcome FROM markets WHERE id = ?", (market_id,)
            ).fetchone()
        if row and row["outcome"] and row["outcome"].upper() in ("YES", "NO"):
            return row["outcome"].upper()
    except Exception as exc:
        logger.error(f"[OutcomeTracker] Ошибка при чтении из БД для {market_id}: {exc}")

    return None


def _resolve_signal(row: dict, resolution: str) -> None:
    """Записывает результат сигнала в БД."""
    was_correct = (row["target_outcome"] or "YES").upper() == resolution.upper()
    now = datetime.now(timezone.utc).isoformat()

    # Вычисляем PnL
    try:
        from core.config_provider import config_provider
        virtual_stake = float(config_provider.get_sync("eval.virtual_stake_usd", default=10.0))
    except Exception:
        virtual_stake = 10.0

    pnl_realized = None
    if resolution != "N/A":
        pnl_realized = 0.0
        strategy = (row["strategy_type"] or "").lower()
        if strategy in ('synthetic_corridor', 'temporal_corridor', 'cross_platform'):
            pnl_realized = virtual_stake * 0.15 if was_correct else -virtual_stake
        elif strategy == 'favourite_compound':
            # Для Favourite Compounding с учетом комиссии 2%
            price_safe = row.get("market_price_at_signal") or 0.95
            if price_safe <= 0 or price_safe >= 1.0:
                price_safe = 0.95
            contracts = virtual_stake / price_safe
            if was_correct:
                pnl_realized = contracts * (1.0 - price_safe) * 0.98
            else:
                pnl_realized = -virtual_stake
        else:
            # Для scout и whale
            price_safe = row.get("market_price_at_signal")
            if price_safe is None and (row.get("estimated_probability") is not None or row.get("predicted_probability") is not None) and row.get("edge") is not None:
                prob_val = row.get("estimated_probability") if row.get("estimated_probability") is not None else row.get("predicted_probability")
                price_safe = prob_val - row["edge"]
            if price_safe is None:
                price_safe = 0.5

            buy_price = price_safe if (row["target_outcome"] or "YES").upper() == 'YES' else (1.0 - price_safe)
            if not (0.001 < buy_price < 0.999):
                buy_price = 0.5

            contracts = virtual_stake / buy_price
            if was_correct:
                pnl_realized = contracts * (1.0 - buy_price)
            else:
                pnl_realized = -virtual_stake

        pnl_realized = round(pnl_realized, 2)

    new_status = 'WIN' if was_correct else 'LOSS'
    outcome_label = 'correct' if was_correct else 'incorrect'
    if resolution == 'N/A':
        new_status = 'ARCHIVED'
        outcome_label = 'unknown'

    from agents.shared.python.db import save_agent_episode, get_memory, save_memory

    # Сохраняем эпизод агента
    agent_name = (row.get("strategy_type") or 'SCOUT').upper()
    predicted_prob = row.get("estimated_probability") or row.get("predicted_probability") or 0.5
    target = (row.get("target_outcome") or "YES").upper()
    try:
        save_agent_episode(
            agent_name=agent_name,
            event_type='signal_resolved',
            summary=f"Рынок '{row['market_title'][:50]}...' закрылся как {resolution}. Прогноз агента: {predicted_prob:.0%}. Результат: {new_status}",
            market_id=row["market_id"],
            market_title=row['market_title'],
            context={
                'predicted_prob': predicted_prob,
                'target': target,
                'resolved_as': resolution
            },
            outcome=outcome_label
        )
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка при сохранении эпизода агента: {e}")

    # Обновляем точность в memory
    if resolution != 'N/A':
        try:
            total_correct_key = f"{agent_name.lower()}_correct_total"
            total_eval_key   = f"{agent_name.lower()}_evaluated_total"
            accuracy_key     = f"{agent_name.lower()}_accuracy_pct"

            prev_correct  = get_memory(total_correct_key) or 0
            prev_total    = get_memory(total_eval_key)    or 0
            new_correct   = prev_correct + (1 if was_correct else 0)
            new_total     = prev_total + 1
            new_accuracy  = round(new_correct / new_total * 100, 1) if new_total > 0 else 0.0

            save_memory(total_correct_key, new_correct, category='fact', priority=7)
            save_memory(total_eval_key,    new_total,   category='fact', priority=7)
            save_memory(accuracy_key,      new_accuracy, category='fact', priority=9)
        except Exception as e:
            logger.error(f"[OutcomeTracker] Ошибка при обновлении memory точности: {e}")

    with get_connection() as conn:
        conn.execute("""
            UPDATE signals
            SET status          = ?,
                resolved_at     = ?,
                resolution_outcome = ?,
                was_profitable  = ?,
                pnl_realized    = ?,
                strategy_type   = COALESCE(strategy_type, 'SCOUT')
            WHERE id = ?
        """, (new_status, now, resolution, int(was_correct), pnl_realized, row["id"]))

        conn.execute("""
            UPDATE markets
            SET outcome = ?
            WHERE id = ?
        """, (resolution, row["market_id"]))

    logger.info(
        f"[OutcomeTracker] {row['id'][:8]}… → {resolution} "
        f"({'WIN' if was_correct else 'LOSS'}) strategy={row['strategy_type']} PnL=${pnl_realized}"
    )


def _update_all_strategy_metrics() -> None:
    """Пересчитывает strategy_metrics для всех стратегий за последние 30 дней."""
    with get_connection() as conn:
        strategies = conn.execute(
            "SELECT DISTINCT strategy_type FROM signals WHERE strategy_type IS NOT NULL"
        ).fetchall()

    for row in strategies:
        _upsert_strategy_metrics(row["strategy_type"])


def _upsert_strategy_metrics(strategy_type: str) -> None:
    """Вычисляет и сохраняет метрики одной стратегии за период rolling-30d."""
    from datetime import timedelta
    import math
    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    period_end = now.strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN was_profitable = 1 THEN 1 ELSE 0 END)      AS wins,
                AVG(edge)                                                  AS avg_edge,
                AVG(CASE WHEN was_profitable IS NOT NULL
                         THEN CAST(was_profitable AS REAL) END)            AS win_rate,
                AVG(pnl_realized)                                          AS avg_realized_pnl,
                -- Brier score: среднее (predicted_prob - outcome)^2
                AVG(
                    CASE WHEN COALESCE(estimated_probability, predicted_probability) IS NOT NULL
                              AND was_profitable IS NOT NULL
                    THEN (COALESCE(estimated_probability, predicted_probability) - CAST(was_profitable AS REAL)) *
                         (COALESCE(estimated_probability, predicted_probability) - CAST(was_profitable AS REAL))
                    END
                ) AS brier_score
            FROM signals
            WHERE strategy_type = ?
              AND created_at >= ?
        """, (strategy_type, period_start)).fetchone()

        if not row or (row["total"] or 0) == 0:
            return

        # Рассчитываем sharpe_ratio
        pnl_rows = conn.execute("""
            SELECT pnl_realized
            FROM signals
            WHERE strategy_type = ?
              AND created_at >= ?
              AND pnl_realized IS NOT NULL
        """, (strategy_type, period_start)).fetchall()
        
        pnl_vals = [r["pnl_realized"] for r in pnl_rows]
        sharpe_ratio = None
        if len(pnl_vals) > 1:
            avg_pnl = sum(pnl_vals) / len(pnl_vals)
            variance = sum((x - avg_pnl) ** 2 for x in pnl_vals) / (len(pnl_vals) - 1)
            std_pnl = math.sqrt(variance)
            if std_pnl > 0.0001:
                sharpe_ratio = (avg_pnl / std_pnl) * math.sqrt(len(pnl_vals))
                sharpe_ratio = round(sharpe_ratio, 4)

        conn.execute("""
            INSERT INTO strategy_metrics
              (strategy_type, period_start, period_end,
               total_signals, resolved_signals, profitable_signals,
               win_rate, avg_edge, avg_realized_pnl, brier_score, sharpe_ratio, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(strategy_type, period_start, period_end) DO UPDATE SET
              total_signals      = excluded.total_signals,
              resolved_signals   = excluded.resolved_signals,
              profitable_signals = excluded.profitable_signals,
              win_rate           = excluded.win_rate,
              avg_edge           = excluded.avg_edge,
              avg_realized_pnl   = excluded.avg_realized_pnl,
              brier_score        = excluded.brier_score,
              sharpe_ratio       = excluded.sharpe_ratio
        """, (
            strategy_type, period_start, period_end,
            row["total"] or 0,
            row["resolved"] or 0,
            row["wins"] or 0,
            row["win_rate"],
            row["avg_edge"],
            row["avg_realized_pnl"],
            row["brier_score"],
            sharpe_ratio,
        ))


def _send_telegram_summary(resolved_items: list[tuple[dict, str]]) -> None:
    """Отправляет сводное оповещение в Telegram о закрытых сигналах."""
    try:
        from services.notifications import send_telegram
        
        lines = ["📊 <b>РЕЗОЛЮЦИЯ СИГНАЛОВ (Outcome Tracker)</b>\n"]
        for row, res in resolved_items:
            was_correct = (row["target_outcome"] or "YES").upper() == res.upper()
            status_emoji = "🟢 WIN" if was_correct else "🔴 LOSS"
            prob_val = row.get("estimated_probability") if row.get("estimated_probability") is not None else row.get("predicted_probability")
            prob_str = f" {prob_val:.0%}" if prob_val is not None else ""
            lines.append(
                f"- <b>{row['market_title'][:40]}...</b>\n"
                f"  Исход: {res} | Прогноз:{prob_str} → {status_emoji} (<i>{row['strategy_type']}</i>)\n"
            )
            
        # Подгружаем актуальный win_rate по стратегиям
        lines.append("\n📈 <b>Точность за последние 30д:</b>")
        with get_connection() as conn:
            metrics = conn.execute("""
                SELECT strategy_type, win_rate, total_signals
                FROM strategy_metrics
                WHERE created_at >= datetime('now', '-1 hour')
            """).fetchall()
            for m in metrics:
                wr = m['win_rate']
                wr_str = f"{wr*100:.1f}%" if wr is not None else "—"
                lines.append(f"- {m['strategy_type']}: <b>{wr_str}</b> ({m['total_signals']} сигн.)")
                
        text = "\n".join(lines)
        send_telegram(text)
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка при отправке сводки в Telegram: {e}")

def _resolve_compound_outcomes() -> int:
    """Авторезолюция compound_opportunities по резолюции рынков. Возвращает количество разрешенных позиций."""
    from agents.shared.python.db import (
        get_active_compound_opportunities, resolve_compound_opportunity,
        get_compound_settings, get_connection
    )
    from services.favourite_compounder import ROICalculator
    
    cfg = get_compound_settings()
    virtual_stake = cfg.get("virtual_stake", 50.0)
    
    # Находим позиции, купленные пользователем
    active_opps = get_active_compound_opportunities()
    bought = [o for o in active_opps if o["status"] in ("BOUGHT", "ALERTED_EXIT")]
    resolved_count = 0

    for opp in bought:
        res = _fetch_resolution(opp["market_id"])
        if res not in ("YES", "NO"):
            continue
            
        # Рассчитываем PnL по правилам оракула
        price = opp["price"]
        target_outcome = opp.get("outcome", "YES")
        was_correct = res == target_outcome
        
        if was_correct:
            contracts = virtual_stake / price
            pnl = contracts * (1.0 - price) * (1.0 - ROICalculator.POLY_FEE_PCT)
        else:
            pnl = -virtual_stake
            
        pnl = round(pnl, 2)
        
        # Обновляем таблицу compound_opportunities
        resolve_compound_opportunity(opp["id"], res, pnl)
        
        # Разрешаем соответствующий сигнал в signals (чтобы обновились strategy_metrics)
        with get_connection() as conn:
            sig_row = conn.execute(
                "SELECT * FROM signals WHERE market_id = ? AND strategy_type = 'FAVOURITE_COMPOUND' "
                "ORDER BY created_at DESC LIMIT 1",
                (opp["market_id"],)
            ).fetchone()
            if sig_row:
                _resolve_signal(dict(sig_row), res)
                
        logger.info(f"[Compound] Резолюция оракула для {opp['id']}: {res} PnL=${pnl:.2f}")
        resolved_count += 1

    return resolved_count
