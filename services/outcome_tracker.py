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
                stats["skipped"] += 1
                continue
            _resolve_signal(row, resolution)
            stats["resolved"] += 1
            resolved_items.append((row, resolution))
        except Exception as exc:
            logger.error(f"[OutcomeTracker] Ошибка резолюции {row['id']}: {exc}")
            stats["errors"] += 1

    if stats["resolved"] > 0:
        _update_all_strategy_metrics()
        _send_telegram_summary(resolved_items)

    logger.info(f"[OutcomeTracker] Итог: {stats}")
    return stats


# ── Внутренние функции ──────────────────────────────────────

def _get_pending_with_closed_market() -> list[dict]:
    """Возвращает PENDING-сигналы, чей рынок уже закрыт (close_time < now)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id, s.market_id, s.target_outcome, s.strategy_type,
                   s.edge, s.confidence, s.created_at, s.estimated_probability,
                   m.title as market_title, m.close_time, m.outcome as market_resolved_outcome
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            WHERE s.status = 'PENDING'
              AND m.close_time < datetime('now')
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

    with get_connection() as conn:
        conn.execute("""
            UPDATE signals
            SET status          = 'ARCHIVED',
                resolved_at     = ?,
                resolution_outcome = ?,
                was_profitable  = ?,
                strategy_type   = COALESCE(strategy_type, 'SCOUT')
            WHERE id = ?
        """, (now, resolution, int(was_correct), row["id"]))

        conn.execute("""
            UPDATE markets
            SET outcome = ?
            WHERE id = ?
        """, (resolution, row["market_id"]))

    logger.info(
        f"[OutcomeTracker] {row['id'][:8]}… → {resolution} "
        f"({'WIN' if was_correct else 'LOSS'}) strategy={row['strategy_type']}"
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
    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(days=30)).isoformat()
    period_end = now.isoformat()

    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN was_profitable = 1 THEN 1 ELSE 0 END)      AS wins,
                AVG(edge)                                                  AS avg_edge,
                AVG(CASE WHEN was_profitable IS NOT NULL
                         THEN CAST(was_profitable AS REAL) END)            AS win_rate,
                -- Brier score: среднее (predicted_prob - outcome)^2
                AVG(
                    CASE WHEN estimated_probability IS NOT NULL
                              AND was_profitable IS NOT NULL
                    THEN (estimated_probability - CAST(was_profitable AS REAL)) *
                         (estimated_probability - CAST(was_profitable AS REAL))
                    END
                ) AS brier_score
            FROM signals
            WHERE strategy_type = ?
              AND created_at >= ?
        """, (strategy_type, period_start)).fetchone()

        if not row or (row["total"] or 0) == 0:
            return

        conn.execute("""
            INSERT INTO strategy_metrics
              (strategy_type, period_start, period_end,
               total_signals, resolved_signals, profitable_signals,
               win_rate, avg_edge, brier_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(strategy_type, period_start, period_end) DO UPDATE SET
              total_signals      = excluded.total_signals,
              resolved_signals   = excluded.resolved_signals,
              profitable_signals = excluded.profitable_signals,
              win_rate           = excluded.win_rate,
              avg_edge           = excluded.avg_edge,
              brier_score        = excluded.brier_score
        """, (
            strategy_type, period_start, period_end,
            row["total"] or 0,
            row["resolved"] or 0,
            row["wins"] or 0,
            row["win_rate"],
            row["avg_edge"],
            row["brier_score"],
        ))


def _send_telegram_summary(resolved_items: list[tuple[dict, str]]) -> None:
    """Отправляет сводное оповещение в Telegram о закрытых сигналах."""
    try:
        from services.notifications import send_telegram
        
        lines = ["📊 <b>РЕЗОЛЮЦИЯ СИГНАЛОВ (Outcome Tracker)</b>\n"]
        for row, res in resolved_items:
            was_correct = (row["target_outcome"] or "YES").upper() == res.upper()
            status_emoji = "🟢 WIN" if was_correct else "🔴 LOSS"
            prob_str = f" {row['estimated_probability']:.0%}" if row.get("estimated_probability") is not None else ""
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
                WHERE period_end >= datetime('now', '-5 minutes')
            """).fetchall()
            for m in metrics:
                lines.append(f"- {m['strategy_type']}: <b>{m['win_rate']*100:.1f}%</b> ({m['total_signals']} сигн.)")
                
        text = "\n".join(lines)
        send_telegram(text)
    except Exception as e:
        logger.error(f"[OutcomeTracker] Ошибка при отправке сводки в Telegram: {e}")
