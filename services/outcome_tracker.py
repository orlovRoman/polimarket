# services/outcome_tracker.py
"""
Outcome Tracker — авторезолюция сигналов по закрытым рынкам.
Запускается каждые 6 часов через scheduler.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
import os
from typing import Optional

from agents.shared.python.db import get_connection
from core.utils import calc_compound_pnl

logger = logging.getLogger("NexusPolyBot.OutcomeTracker")

# ── Публичный API ───────────────────────────────────────────

def run_resolution_cycle() -> dict:
    """
    Главная точка входа. Находит все PENDING-сигналы с закрытым рынком,
    запрашивает resolution через Polymarket API, обновляет signals и strategy_metrics.
    Возвращает статистику прогона: {resolved, skipped, errors}.
    """
    try:
        from agents.shared.python.db import cleanup_stale_signals, cleanup_old_episodes
        
        # (Resolution for compound opportunities is handled below via _resolve_compound_outcomes)
            
        cleanup_stale_signals()
        deleted_episodes = cleanup_old_episodes(days=90)
        if deleted_episodes > 0:
            logger.info(f"[OutcomeTracker] Очищено старых эпизодов агентов (days=90): {deleted_episodes}")
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка при очистке старых сигналов/эпизодов: {e}")

    stats = {"resolved": 0, "skipped": 0, "errors": 0}
    
    # Сначала всегда запускаем резолюцию Favourite Compounding позиций
    resolved_compounds = 0
    try:
        resolved_compounds = _resolve_compound_outcomes()
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка резолюции compound-позиций: {e}", exc_info=True)

    pending = _get_pending_with_closed_market()
    logger.info(f"[OutcomeTracker] Найдено {len(pending)} сигналов для резолюции.")

    if not pending:
        if resolved_compounds > 0:
            _update_all_strategy_metrics()
        logger.info(f"[OutcomeTracker] Итог: {stats}")
        return stats

    resolved_items = []
    for row in pending:
        try:
            resolution = _fetch_resolution(row["market_id"])
            if resolution is None:
                try:
                    created = _parse_created_at(row["created_at"])
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

    if stats["resolved"] > 0 or resolved_compounds > 0:
        _update_all_strategy_metrics()
        if stats["resolved"] > 0:
            _send_telegram_summary(resolved_items)

    logger.info(f"[OutcomeTracker] Итог: {stats}")
    return stats


def _parse_created_at(created_str: str) -> datetime:
    """Надежный парсер даты создания сигнала для обеспечения совместимости."""
    s = created_str.strip().replace("Z", "+00:00").replace(" ", "T")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
            WHERE s.status IN ('PENDING', 'ARCHIVED')
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
        if strategy == 'favourite_compound':
            # Для Favourite Compounding с учетом комиссии 2% и своей ставки
            try:
                from agents.shared.python.db import get_compound_settings
                compound_stake = float(get_compound_settings().get("virtual_stake", virtual_stake))
            except Exception:
                compound_stake = virtual_stake
            price_safe = row.get("market_price_at_signal") or 0.95
            if price_safe <= 0 or price_safe >= 1.0:
                price_safe = 0.95
            contracts = compound_stake / price_safe
            if was_correct:
                pnl_realized = contracts * (1.0 - price_safe) * 0.98
            else:
                pnl_realized = -compound_stake
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
                pnl_realized = contracts * (1.0 - buy_price) * 0.98
            else:
                pnl_realized = -virtual_stake

        pnl_realized = round(pnl_realized, 2)

    new_status = 'WIN' if was_correct else 'LOSS'
    outcome_label = 'correct' if was_correct else 'incorrect'
    if resolution == 'N/A':
        new_status = 'ARCHIVED'
        outcome_label = 'unknown'

    from agents.shared.python.db import save_agent_episode, get_memory, save_memory, update_episodes_for_market

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

    try:
        update_episodes_for_market(row["market_id"], resolution)
    except Exception as ep_err:
        logger.error(f"[OutcomeTracker] Error updating episodes for market {row['market_id']}: {ep_err}")

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

        # Вычисляем ECE (calibration_error)
        ece_rows = conn.execute("""
            SELECT 
                COALESCE(estimated_probability, predicted_probability) AS prob,
                was_profitable
            FROM signals
            WHERE strategy_type = ?
              AND created_at >= ?
              AND COALESCE(estimated_probability, predicted_probability) IS NOT NULL
              AND was_profitable IS NOT NULL
        """, (strategy_type, period_start)).fetchall()
        
        ece_val = None
        if ece_rows:
            try:
                records = [{"prob": r["prob"], "actual": float(r["was_profitable"])} for r in ece_rows]
            except (KeyError, TypeError, IndexError):
                records = []
            n_bins = 10
            total_signals = len(records)
            ece = 0.0
            for i in range(n_bins):
                bin_lower = i / n_bins
                bin_upper = (i + 1) / n_bins
                
                if i == n_bins - 1:
                    bin_records = [r for r in records if bin_lower <= r["prob"] <= bin_upper]
                else:
                    bin_records = [r for r in records if bin_lower <= r["prob"] < bin_upper]
                    
                if not bin_records:
                    continue
                bin_size = len(bin_records)
                avg_predicted = sum(r["prob"] for r in bin_records) / bin_size
                avg_actual = sum(r["actual"] for r in bin_records) / bin_size
                bin_diff = abs(avg_predicted - avg_actual)
                ece += (bin_size / total_signals) * bin_diff
            ece_val = round(ece, 4)

        conn.execute("""
            INSERT INTO strategy_metrics
              (strategy_type, period_start, period_end,
               total_signals, resolved_signals, profitable_signals,
               win_rate, avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(strategy_type, period_start, period_end) DO UPDATE SET
              total_signals      = excluded.total_signals,
              resolved_signals   = excluded.resolved_signals,
              profitable_signals = excluded.profitable_signals,
              win_rate           = excluded.win_rate,
              avg_edge           = excluded.avg_edge,
              avg_realized_pnl   = excluded.avg_realized_pnl,
              brier_score        = excluded.brier_score,
              calibration_error  = excluded.calibration_error,
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
            ece_val,
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

def _resolve_chain_bets_for_opportunity(opp: dict, res: str) -> None:
    from agents.shared.python.db import get_connection
    target_outcome = opp.get("outcome", "YES")
    was_correct = res == target_outcome
    price = float(opp.get("price", 0.95))
    
    try:
        with get_connection() as conn:
            bets = conn.execute("SELECT * FROM compound_chain_bets WHERE opp_id = ? AND status = 'PENDING'", (opp["id"],)).fetchall()
            if not bets:
                return
            for bet in bets:
                bet_id = bet["id"]
                chain_id = bet["chain_id"]
                
                chain = conn.execute("SELECT * FROM compound_chains WHERE id = ?", (chain_id,)).fetchone()
                if not chain: continue
                
                current_stake = float(chain["current_stake"])
                current_step = int(chain["current_step"])
                target_steps = int(chain["target_steps"])
                
                if res == "N/A":
                    # Рынок был отменен или истек по таймауту. Ставка возвращается.
                    conn.execute("UPDATE compound_chain_bets SET status = 'REFUNDED', payout = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (current_stake, bet_id))
                    conn.execute(
                        "UPDATE compound_chains SET status = 'WAITING_NEXT', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (chain_id,)
                    )
                    logger.info(f"[Chains] Цепочка #{chain_id}: рынок отменен (REFUNDED). Возврат к ожиданию следующего рынка.")
                elif was_correct:
                    contracts = current_stake / price
                    gross_payout = contracts * 1.0
                    profit = gross_payout - current_stake
                    # Polymarket fee 2%
                    profit_after_fee = profit * (1 - 0.02)
                    new_stake = current_stake + profit_after_fee
                    payout = new_stake
                    
                    conn.execute("UPDATE compound_chain_bets SET status = 'WON', payout = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (payout, bet_id))
                    
                    new_step = current_step + 1
                    if new_step >= target_steps:
                        new_status = 'COMPLETED'
                    else:
                        new_status = 'WAITING_NEXT'
                        
                    conn.execute(
                        "UPDATE compound_chains SET status = ?, current_stake = ?, current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_status, new_stake, new_step, chain_id)
                    )
                    logger.info(f"[Chains] Цепочка #{chain_id} выиграла шаг {new_step}. Новый стейк: ${new_stake:.2f}. Статус: {new_status}")
                else:
                    conn.execute("UPDATE compound_chain_bets SET status = 'LOST', payout = 0, resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (bet_id,))
                    conn.execute(
                        "UPDATE compound_chains SET status = 'FAILED', current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (current_step + 1, chain_id)
                    )
                    logger.info(f"[Chains] Цепочка #{chain_id} проиграла на шаге {current_step + 1}. Статус: FAILED")
            conn.commit()
    except Exception as e:
        logger.error(f"[Chains] Ошибка авторезолюции цепочек: {e}", exc_info=True)

def _resolve_compound_outcomes() -> int:
    """Авторезолюция compound_opportunities по резолюции рынков. Возвращает количество разрешенных позиций."""
    from agents.shared.python.db import (
        get_active_compound_opportunities, resolve_compound_opportunity,
        get_compound_settings, get_connection
    )
    from services.favourite_compounder import ROICalculator
    
    cfg = get_compound_settings()
    virtual_stake = cfg.get("virtual_stake", 50.0)
    
    # Находим все неразрешенные compound-позиции, у которых наступило время закрытия или которые уже разрешены оракулом
    # Используем виртуальную ставку только из настроек стратегии

    with get_connection() as conn:
        to_resolve_rows = conn.execute("""
            SELECT o.*, m.outcome as market_resolved_outcome
            FROM compound_opportunities o
            LEFT JOIN markets m ON o.market_id = m.id
            WHERE o.status != 'RESOLVED'
              AND datetime(o.close_time) < datetime('now', '-15 minutes')
        """).fetchall()
    to_resolve = [dict(r) for r in to_resolve_rows]
    resolved_count = 0

    for opp in to_resolve:
        res = _fetch_resolution(opp["market_id"])
        if res not in ("YES", "NO"):
            try:
                # Fallback: if market is older than config days, cancel it
                created = _parse_created_at(opp["created_at"])
                age_days = (datetime.now(timezone.utc) - created).days
                timeout_days = int(os.getenv("COMPOUND_TIMEOUT_DAYS", "7"))
                if age_days >= timeout_days:
                    res = "N/A"
                    logger.warning(f"[Compound] Сделка {opp['id']} отменена по таймауту ({timeout_days} дней)")
                else:
                    continue
            except Exception:
                continue
            
        # 1. Если это ручная сделка в виртуальном портфеле, разрешаем её
        resolved_manual = False
        if opp.get("virtual_bought_price") is not None:
            from agents.shared.python.db import resolve_compound_opportunity_manual_portfolio
            resolve_compound_opportunity_manual_portfolio(opp["id"], res)
            resolved_manual = True
            
        # 2. Если это авто-сделка или ручная сделка, разрешаем её с расчетом PnL
        opp_status = opp.get("status")
        if opp_status in ("BOUGHT", "ALERTED", "ALERTED_EXIT") or resolved_manual:
            price = opp["price"]
            target_outcome = opp.get("outcome", "YES")
            was_correct = res == target_outcome
            
            exit_price = 1.0 if was_correct else 0.0
            pnl = calc_compound_pnl(virtual_stake, price, exit_price)
            
            # Обновляем таблицу compound_opportunities
            resolve_compound_opportunity(opp["id"], res, pnl)
            
            # Разрешаем соответствующий сигнал в signals (чтобы обновились strategy_metrics)
            with get_connection() as conn:
                sig_row = conn.execute(
                    "SELECT * FROM signals WHERE market_id = ? AND UPPER(strategy_type) = 'FAVOURITE_COMPOUND' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (opp["market_id"],)
                ).fetchone()
                if sig_row:
                    _resolve_signal(dict(sig_row), res)
            logger.info(f"[Compound] Резолюция оракула для {opp['id']}: {res} Auto PnL=${pnl:.2f}")
        elif opp_status == "NEW" or resolved_manual:
            # Если статус NEW или была ручная сделка, закрываем саму возможность с 0 PnL
            resolve_compound_opportunity(opp["id"], res, 0.0)
            logger.info(f"[Compound] Резолюция оракула для {opp['id']}: {res} (без PnL / только ручная сделка)")
            
        # 3. Автоматические цепочки
        _resolve_chain_bets_for_opportunity(opp, res)
            
        resolved_count += 1

    if resolved_count > 0:
        from agents.shared.python.db import reallocate_pending_opportunities
        reallocate_pending_opportunities()

    return resolved_count
