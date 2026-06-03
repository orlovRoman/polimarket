import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from core.eval.signal_logger import StrategyType
from core.eval.threshold_calibrator import CalibrationSuggestion
from config import DB_PATH

logger = logging.getLogger("CalibrationStore")

class CalibrationRecord:
    def __init__(
        self,
        id: int,
        strategy_type: str,
        param_name: str,
        param_value: float,
        previous_value: float,
        reason: str,
        auto_applied: bool,
        updated_at: str
    ):
        self.id = id
        self.strategy_type = strategy_type
        self.param_name = param_name
        self.param_value = param_value
        self.previous_value = previous_value
        self.reason = reason
        self.auto_applied = auto_applied
        self.updated_at = updated_at

class CalibrationStore:
    """
    Управляет сохранением истории изменений параметров в БД,
    а также операциями применения (apply) и отката (rollback).
    """

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    async def save_suggestion(
        self,
        suggestion: CalibrationSuggestion,
        strategy_type: StrategyType,
        auto_apply: bool
    ) -> int:
        """
        Сохраняет предложение по калибровке в БД.
        Возвращает ID созданной записи.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO calibration_params (
                        strategy_type, param_name, param_value, previous_value, reason, auto_applied
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    strategy_type.value,
                    suggestion.param_name,
                    suggestion.suggested_value,
                    suggestion.current_value,
                    suggestion.reason,
                    1 if auto_apply else 0
                ))
                conn.commit()
                row_id = cursor.lastrowid
                
                # Если auto_apply включен, мы инвалидируем кэш ConfigProvider
                if auto_apply:
                    try:
                        from core.config_provider import ConfigProvider
                        ConfigProvider.invalidate_cache()
                    except ImportError:
                        pass
                        
                return row_id
        except Exception as e:
            logger.error(f"Ошибка при сохранении предложения калибровки: {e}", exc_info=True)
            return -1

    async def apply_suggestion(self, suggestion_id: int) -> bool:
        """
        Применяет ранее отложенное (auto_applied=0) предложение по калибровке:
        устанавливает флаг auto_applied=1 и инвалидирует кэш.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, param_name FROM calibration_params WHERE id = ?
                """, (suggestion_id,))
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Предложение калибровки с ID {suggestion_id} не найдено.")
                    return False
                    
                cursor.execute("""
                    UPDATE calibration_params 
                    SET auto_applied = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (suggestion_id,))
                conn.commit()
                
                # Инвалидируем кэш
                try:
                    from core.config_provider import ConfigProvider
                    ConfigProvider.invalidate_cache()
                except ImportError:
                    pass
                    
                logger.info(f"Предложение калибровки #{suggestion_id} успешно применено.")
                return True
        except Exception as e:
            logger.error(f"Ошибка при применении предложения #{suggestion_id}: {e}", exc_info=True)
            return False

    async def get_history(self, param_name: str, last_n: int = 10) -> List[CalibrationRecord]:
        """
        Возвращает историю изменения указанного параметра (только примененные/активные записи).
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, strategy_type, param_name, param_value, previous_value, reason, auto_applied, updated_at
                    FROM calibration_params
                    WHERE param_name = ?
                      AND auto_applied = 1
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                """, (param_name, last_n))
                rows = cursor.fetchall()
                
                return [CalibrationRecord(
                    id=r["id"],
                    strategy_type=r["strategy_type"],
                    param_name=r["param_name"],
                    param_value=r["param_value"],
                    previous_value=r["previous_value"],
                    reason=r["reason"],
                    auto_applied=bool(r["auto_applied"]),
                    updated_at=r["updated_at"]
                ) for r in rows]
        except Exception as e:
            logger.error(f"Ошибка чтения истории калибровки для {param_name}: {e}", exc_info=True)
            return []

    async def get_strategy_history(self, strategy_type: str, last_n: int = 10) -> List[CalibrationRecord]:
        """
        Возвращает историю изменений и предложений калибровки для указанной стратегии.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, strategy_type, param_name, param_value, previous_value, reason, auto_applied, updated_at
                    FROM calibration_params
                    WHERE strategy_type = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                """, (strategy_type, last_n))
                rows = cursor.fetchall()
                
                return [CalibrationRecord(
                    id=r["id"],
                    strategy_type=r["strategy_type"],
                    param_name=r["param_name"],
                    param_value=r["param_value"],
                    previous_value=r["previous_value"],
                    reason=r["reason"],
                    auto_applied=bool(r["auto_applied"]),
                    updated_at=r["updated_at"]
                ) for r in rows]
        except Exception as e:
            logger.error(f"Ошибка чтения истории калибровки для стратегии {strategy_type}: {e}", exc_info=True)
            return []


    async def get_latest_applied_value(self, param_name: str, strategy_type: Optional[str] = None) -> Optional[float]:
        """
        Возвращает последнее примененное значение для параметра (с опциональной фильтрацией по стратегии).
        """
        return self.get_latest_applied_value_sync(param_name, strategy_type)

    def get_latest_applied_value_sync(self, param_name: str, strategy_type: Optional[str] = None) -> Optional[float]:
        """
        Синхронно возвращает последнее примененное значение для параметра (с опциональной фильтрацией по стратегии).
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if strategy_type:
                    cursor.execute("""
                        SELECT param_value FROM calibration_params
                        WHERE param_name = ? AND strategy_type = ?
                          AND auto_applied = 1
                        ORDER BY updated_at DESC, id DESC
                        LIMIT 1
                    """, (param_name, strategy_type))
                else:
                    cursor.execute("""
                        SELECT param_value FROM calibration_params
                        WHERE param_name = ?
                          AND auto_applied = 1
                        ORDER BY updated_at DESC, id DESC
                        LIMIT 1
                    """, (param_name,))
                row = cursor.fetchone()
                if row:
                    return row["param_value"]
        except Exception as e:
            logger.error(f"Ошибка чтения последнего примененного значения для {param_name} ({strategy_type}): {e}")
        return None

    async def rollback(self, suggestion_id: int) -> bool:
        """
        Откатывает изменение: записывает НОВОЕ значение в БД (которое совпадает с предыдущим значением).
        Это сохраняет историю изменений append-only.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT strategy_type, param_name, param_value, previous_value
                    FROM calibration_params 
                    WHERE id = ? AND auto_applied = 1
                """, (suggestion_id,))
                row = cursor.fetchone()
                
                if not row:
                    logger.warning(f"Примененное предложение #{suggestion_id} для отката не найдено.")
                    return False
                    
                strategy_type = row["strategy_type"]
                param_name = row["param_name"]
                current_val = row["param_value"]
                prev_val = row["previous_value"]
                
                # Добавляем новую append-only запись, которая откатывает значение назад
                cursor.execute("""
                    INSERT INTO calibration_params (
                        strategy_type, param_name, param_value, previous_value, reason, auto_applied
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    strategy_type,
                    param_name,
                    prev_val,
                    current_val,
                    f"Откат изменения #{suggestion_id} (возврат с {current_val} на {prev_val})",
                    1  # Сразу активно/применено
                ))
                conn.commit()
                
                # Инвалидируем кэш ConfigProvider
                try:
                    from core.config_provider import ConfigProvider
                    ConfigProvider.invalidate_cache()
                except ImportError:
                    pass
                    
                logger.info(f"Успешный откат изменения #{suggestion_id}: параметр {param_name} возвращен на {prev_val}.")
                return True
        except Exception as e:
            logger.error(f"Ошибка при откате предложения #{suggestion_id}: {e}", exc_info=True)
            return False
