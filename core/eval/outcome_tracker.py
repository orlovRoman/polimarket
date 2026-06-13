import logging
import sqlite3
import json
from datetime import datetime, timezone
from typing import List, NamedTuple

from core.eval.polymarket_resolution_client import PolymarketResolutionClient
from core.eval.signal_logger import SignalLogger, StrategyType
from core.eval.metrics_repository import MetricsRepository
from core.eval.threshold_calibrator import ThresholdCalibrator
from config import DB_PATH

logger = logging.getLogger("NexusPolyBot.OutcomeTracker")

class PendingSignal(NamedTuple):
    signal_id: str
    market_id: str          # = condition_id для Polymarket API
    strategy_type: str
    close_time: datetime

class OutcomeTracker:
    def __init__(
        self,
        resolution_client: PolymarketResolutionClient | None = None,
        signal_logger: SignalLogger | None = None,
        metrics_repo: MetricsRepository | None = None,
        calibrator: ThresholdCalibrator | None = None,
    ):
        self._client = resolution_client or PolymarketResolutionClient()
        self._signal_logger = signal_logger or SignalLogger()
        self._metrics_repo = metrics_repo or MetricsRepository()
        self._calibrator = calibrator or ThresholdCalibrator()

    def _get_pending_expired(self) -> List[PendingSignal]:
        """
        Возвращает PENDING сигналы, у которых close_time < now().
        Лимит 200 за цикл — защита от burst при первом запуске.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, market_id, strategy_type, close_time
                FROM signals
                WHERE status = 'PENDING'
                  AND close_time IS NOT NULL
                  AND close_time < ?
                ORDER BY close_time ASC
                LIMIT 200
            """, (now_iso,))
            rows = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"[OT] Ошибка чтения PENDING сигналов: {e}", exc_info=True)
            return []

        result = []
        for row in rows:
            try:
                ct = datetime.fromisoformat(row["close_time"])
            except Exception:
                continue
            result.append(PendingSignal(
                signal_id=row["id"],
                market_id=row["market_id"],
                strategy_type=row["strategy_type"] or "",
                close_time=ct
            ))
        return result

    def run_cycle(self) -> dict:
        """
        Один прогон трекера. Возвращает статистику цикла для логирования.
        Синхронный — вызывается из APScheduler.
        """
        stats = {"checked": 0, "resolved": 0, "skipped": 0, "errors": 0}
        pending = self._get_pending_expired()
        logger.info(f"[OT] Найдено {len(pending)} PENDING-сигналов с истёкшим close_time")

        affected_strategies: set[str] = set()

        for signal in pending:
            stats["checked"] += 1
            resolution = self._client.fetch_resolution(signal.market_id)

            if resolution is None:
                logger.warning(f"[OT] API error для market_id={signal.market_id}, пропускаем")
                stats["errors"] += 1
                continue

            if not resolution.is_resolved:
                stats["skipped"] += 1
                continue

            outcome = resolution.winning_outcome or "N/A"
            self._signal_logger.log_resolution(
                signal_id=signal.signal_id,
                resolution_outcome=outcome,
                resolution_price=resolution.resolution_price,
            )
            affected_strategies.add(signal.strategy_type.lower())
            stats["resolved"] += 1

        # Обновляем метрики только для затронутых стратегий
        for st_value in affected_strategies:
            try:
                st = StrategyType(st_value)
            except ValueError:
                logger.warning(f"[OT] Неизвестный strategy_type='{st_value}', пропускаем пересчёт метрик")
                continue

            self._metrics_repo.compute_and_store_metrics_sync(st)
            logger.info(f"[OT] Метрики пересчитаны для {st_value}")

        # Пересчёт порогов калибровки
        if affected_strategies:
            try:
                self._calibrator.recalibrate()
                logger.info("[OT] Пороги калибровки обновлены")
            except Exception as e:
                logger.error(f"[OT] Ошибка калибровки: {e}", exc_info=True)

        # Сохраняем статистику прогона в БД для UI
        try:
            stats_to_save = dict(stats)
            stats_to_save["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            conn.execute("""
                INSERT INTO memory (key, value, updated_at) 
                VALUES ('outcome_tracker_last_run', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET 
                    value=excluded.value, 
                    updated_at=CURRENT_TIMESTAMP
            """, (json.dumps(stats_to_save),))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[OT] Ошибка сохранения статистики прогона в memory: {e}", exc_info=True)

        logger.info(f"[OT] Цикл завершён: {stats}")
        return stats
