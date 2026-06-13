import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from core.eval.signal_logger import StrategyType
from core.eval.metrics_calculator import SignalRecord, StrategyMetrics, calculate_metrics
from config import DB_PATH

logger = logging.getLogger("NexusPolyBot.MetricsRepository")

class MetricsRepository:
    """
    Класс для сохранения и загрузки результатов анализа предсказаний в БД.
    Осуществляет связь между математическим ядром оценки и базой данных.
    """

    @contextmanager
    def _get_connection(self):
        """Контекстный менеджер, гарантирующий закрытие SQLite-соединения."""
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _compute_and_store_metrics_impl(
        self,
        strategy_type: StrategyType,
        period_days: int = 30
    ) -> Optional[StrategyMetrics]:
        """
        Общая реализация расчета и сохранения метрик.
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=period_days)
        
        start_str = start_time.isoformat()
        end_str = now.isoformat()
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT predicted_probability, status, edge_at_signal, pnl_realized
                    FROM signals
                    WHERE strategy_type = ?
                      AND status IN ('WIN', 'LOSS')
                      AND resolved_at >= ?
                """, (strategy_type.value, start_str))
                rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка чтения сигналов для расчета метрик: {e}", exc_info=True)
            return None

        if not rows:
            logger.info(f"Нет разрешенных сигналов за последние {period_days} дней для стратегии {strategy_type.value}.")
            return None

        # Конвертируем строки БД в SignalRecord
        records = []
        for row in rows:
            is_win = row["status"] == "WIN"
            pred_prob = row["predicted_probability"] if row["predicted_probability"] is not None else 0.5
            edge = row["edge_at_signal"] if row["edge_at_signal"] is not None else 0.0
            
            records.append(SignalRecord(
                predicted_probability=pred_prob,
                resolution_outcome=is_win,
                edge_at_signal=edge,
                pnl_realized=row["pnl_realized"]
            ))

        metrics = calculate_metrics(records)
        if not metrics:
            return None

        # Записываем метрики в БД (идемпотентно)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO strategy_metrics (
                        strategy_type, period_start, period_end, total_signals, resolved_signals,
                        profitable_signals, win_rate, avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(strategy_type, period_start, period_end) DO UPDATE SET
                        total_signals=excluded.total_signals,
                        resolved_signals=excluded.resolved_signals,
                        profitable_signals=excluded.profitable_signals,
                        win_rate=excluded.win_rate,
                        avg_edge=excluded.avg_edge,
                        avg_realized_pnl=excluded.avg_realized_pnl,
                        brier_score=excluded.brier_score,
                        calibration_error=excluded.calibration_error,
                        sharpe_ratio=excluded.sharpe_ratio,
                        created_at=CURRENT_TIMESTAMP
                """, (
                    strategy_type.value,
                    start_str,
                    end_str,
                    metrics.total_signals,
                    metrics.resolved_signals,
                    metrics.profitable_signals,
                    metrics.win_rate,
                    metrics.avg_edge,
                    metrics.avg_realized_pnl,
                    metrics.brier_score,
                    metrics.calibration_error,
                    metrics.sharpe_ratio
                ))
                conn.commit()
                logger.info(f"Метрики для {strategy_type.value} успешно сохранены в strategy_metrics.")
        except Exception as e:
            logger.error(f"Ошибка сохранения метрик для стратегии {strategy_type.value} в БД: {e}", exc_info=True)

        return metrics

    async def compute_and_store_metrics(
        self,
        strategy_type: StrategyType,
        period_days: int = 30
    ) -> Optional[StrategyMetrics]:
        """
        Асинхронная обертка для расчета и сохранения метрик.
        """
        return await asyncio.to_thread(self._compute_and_store_metrics_impl, strategy_type, period_days)

    def get_latest_metrics_sync(self, strategy_type: StrategyType) -> Optional[StrategyMetrics]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT total_signals, resolved_signals, profitable_signals, win_rate, 
                           avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio
                    FROM strategy_metrics
                    WHERE strategy_type = ?
                    ORDER BY period_end DESC, id DESC
                    LIMIT 1
                """, (strategy_type.value,))
                row = cursor.fetchone()
                if not row:
                    return None
                    
                return StrategyMetrics(
                    total_signals=row["total_signals"],
                    resolved_signals=row["resolved_signals"],
                    profitable_signals=row["profitable_signals"],
                    win_rate=row["win_rate"],
                    avg_edge=row["avg_edge"],
                    avg_realized_pnl=row["avg_realized_pnl"],
                    brier_score=row["brier_score"],
                    calibration_error=row["calibration_error"],
                    sharpe_ratio=row["sharpe_ratio"]
                )
        except Exception as e:
            logger.error(f"Ошибка чтения последних метрик для {strategy_type.value}: {e}", exc_info=True)
            return None

    async def get_latest_metrics(self, strategy_type: StrategyType) -> Optional[StrategyMetrics]:
        """
        Возвращает последнюю запись метрик для указанной стратегии.
        """
        return await asyncio.to_thread(self.get_latest_metrics_sync, strategy_type)

    def get_metrics_trend_sync(
        self,
        strategy_type: StrategyType,
        last_n_periods: int = 4
    ) -> List[StrategyMetrics]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT total_signals, resolved_signals, profitable_signals, win_rate, 
                           avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio
                    FROM strategy_metrics
                    WHERE strategy_type = ?
                    ORDER BY period_end DESC, id DESC
                    LIMIT ?
                """, (strategy_type.value, last_n_periods))
                rows = cursor.fetchall()
                
                trend = []
                for row in reversed(rows): # возвращаем от старых к новым
                    trend.append(StrategyMetrics(
                        total_signals=row["total_signals"],
                        resolved_signals=row["resolved_signals"],
                        profitable_signals=row["profitable_signals"],
                        win_rate=row["win_rate"],
                        avg_edge=row["avg_edge"],
                        avg_realized_pnl=row["avg_realized_pnl"],
                        brier_score=row["brier_score"],
                        calibration_error=row["calibration_error"],
                        sharpe_ratio=row["sharpe_ratio"]
                    ))
                return trend
        except Exception as e:
            logger.error(f"Ошибка чтения тренда метрик для {strategy_type.value}: {e}", exc_info=True)
            return []

    async def get_metrics_trend(
        self,
        strategy_type: StrategyType,
        last_n_periods: int = 4
    ) -> List[StrategyMetrics]:
        """
        Возвращает историю изменения метрик для отслеживания динамики (тренда).
        """
        return await asyncio.to_thread(self.get_metrics_trend_sync, strategy_type, last_n_periods)

    def compute_and_store_metrics_sync(
        self,
        strategy_type: StrategyType,
        period_days: int = 30
    ) -> Optional[StrategyMetrics]:
        """
        Синхронная версия compute_and_store_metrics для вызова из OutcomeTracker/APScheduler.
        """
        return self._compute_and_store_metrics_impl(strategy_type, period_days)

    def get_latest_metrics_sync(self, strategy_type: StrategyType) -> Optional[StrategyMetrics]:
        """
        Синхронная версия get_latest_metrics.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT total_signals, resolved_signals, profitable_signals, win_rate, 
                           avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio
                    FROM strategy_metrics
                    WHERE strategy_type = ?
                    ORDER BY period_end DESC, id DESC
                    LIMIT 1
                """, (strategy_type.value,))
                row = cursor.fetchone()
                if not row:
                    return None
                    
                return StrategyMetrics(
                    total_signals=row["total_signals"],
                    resolved_signals=row["resolved_signals"],
                    profitable_signals=row["profitable_signals"],
                    win_rate=row["win_rate"],
                    avg_edge=row["avg_edge"],
                    avg_realized_pnl=row["avg_realized_pnl"],
                    brier_score=row["brier_score"],
                    calibration_error=row["calibration_error"],
                    sharpe_ratio=row["sharpe_ratio"]
                )
        except Exception as e:
            logger.error(f"Ошибка чтения последних метрик для {strategy_type.value} (sync): {e}", exc_info=True)
            return None

    def get_metrics_trend_sync(
        self,
        strategy_type: StrategyType,
        last_n_periods: int = 4
    ) -> List[StrategyMetrics]:
        """
        Синхронная версия get_metrics_trend.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT total_signals, resolved_signals, profitable_signals, win_rate, 
                           avg_edge, avg_realized_pnl, brier_score, calibration_error, sharpe_ratio
                    FROM strategy_metrics
                    WHERE strategy_type = ?
                    ORDER BY period_end DESC, id DESC
                    LIMIT ?
                """, (strategy_type.value, last_n_periods))
                rows = cursor.fetchall()
                
                trend = []
                for row in reversed(rows):
                    trend.append(StrategyMetrics(
                        total_signals=row["total_signals"],
                        resolved_signals=row["resolved_signals"],
                        profitable_signals=row["profitable_signals"],
                        win_rate=row["win_rate"],
                        avg_edge=row["avg_edge"],
                        avg_realized_pnl=row["avg_realized_pnl"],
                        brier_score=row["brier_score"],
                        calibration_error=row["calibration_error"],
                        sharpe_ratio=row["sharpe_ratio"]
                    ))
                return trend
        except Exception as e:
            logger.error(f"Ошибка чтения тренда метрик для {strategy_type.value} (sync): {e}", exc_info=True)
            return []
