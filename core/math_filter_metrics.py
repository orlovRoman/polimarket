# core/math_filter_metrics.py
"""
Логирует каждый вызов math_pre_filter в SQLite.
Позволяет измерить precision/recall через /stats команду в боте.
"""
from __future__ import annotations
import logging
from datetime import datetime
from core.math_filter import MathFilterResult, FilterDecision

logger = logging.getLogger("math_filter_metrics")


def log_filter_result(
    market_a_id: str,
    market_b_id: str,
    result: MathFilterResult,
    outcome: str | None = None,  # "profitable" | "loss" | None (pending)
) -> None:
    """Записывает результат в таблицу math_filter_log."""
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS math_filter_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_a_id TEXT,
                    market_b_id TEXT,
                    decision TEXT,
                    arbitrage_type TEXT,
                    spread_pct REAL,
                    has_arbitrage INTEGER,
                    outcome TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO math_filter_log
                (market_a_id, market_b_id, decision, arbitrage_type,
                 spread_pct, has_arbitrage, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                market_a_id, market_b_id,
                result.decision.value,
                result.arbitrage_type,
                result.spread_pct,
                int(result.has_arbitrage),
                outcome,
            ))
    except Exception as e:
        logger.warning(f"[metrics] Ошибка записи: {e}")


def get_stats() -> dict:
    """Возвращает агрегированную статистику для /stats команды бота."""
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT
                    decision,
                    arbitrage_type,
                    COUNT(*) as cnt,
                    AVG(spread_pct) as avg_spread,
                    SUM(has_arbitrage) as confirmed
                FROM math_filter_log
                WHERE created_at > datetime('now', '-7 days')
                GROUP BY decision, arbitrage_type
                ORDER BY cnt DESC
            """).fetchall()
            return {"rows": [dict(r) for r in rows]}
    except Exception as e:
        logger.warning(f"[metrics] Ошибка чтения stats: {e}")
        return {}
