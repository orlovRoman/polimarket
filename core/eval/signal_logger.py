import json
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator

from config import DB_PATH

logger = logging.getLogger("SignalLogger")

class StrategyType(str, Enum):
    SCOUT = 'scout'
    SYNTHETIC_CORRIDOR = 'synthetic_corridor'
    TEMPORAL_CORRIDOR = 'temporal_corridor'
    CROSS_PLATFORM = 'cross_platform'
    WHALE = 'whale'

class SignalPayload(BaseModel):
    signal_id: str
    strategy_type: StrategyType
    market_id: str
    predicted_probability: float = Field(..., ge=0.0, le=1.0)
    market_price_at_signal: float = Field(..., ge=0.0, le=1.0)
    edge_at_signal: float = Field(..., ge=-1.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('predicted_probability', 'market_price_at_signal', 'edge_at_signal', mode='before')
    @classmethod
    def convert_float(cls, v: Any) -> float:
        return float(v)

class ResolutionPayload(BaseModel):
    signal_id: str
    resolution_outcome: str
    resolution_price: float = Field(..., ge=0.0, le=1.0)
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('resolution_price', mode='before')
    @classmethod
    def convert_float(cls, v: Any) -> float:
        return float(v)

class SignalLogger:
    """
    Записывает предсказание в момент генерации сигнала и результаты его резолюции.
    Не содержит логики принятия решений.
    """

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def log_signal(
        self,
        signal_id: str,
        strategy_type: StrategyType,
        market_id: str,
        predicted_probability: float,
        market_price_at_signal: float,
        edge_at_signal: float,
        metadata: Dict[str, Any],
        created_at: Optional[datetime] = None
    ) -> None:
        """
        Записывает сигнал в базу данных в момент генерации.
        """
        try:
            payload = SignalPayload(
                signal_id=signal_id,
                strategy_type=strategy_type,
                market_id=market_id,
                predicted_probability=predicted_probability,
                market_price_at_signal=market_price_at_signal,
                edge_at_signal=edge_at_signal,
                metadata=metadata,
                created_at=created_at or datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Ошибка валидации данных сигнала: {e}")
            return

        try:
            # Извлекаем метаданные для полей signals
            target_outcome = payload.metadata.get("target_outcome", "YES")
            priority = payload.metadata.get("priority", "medium")
            summary = payload.metadata.get("summary", f"Signal {payload.strategy_type} for market {payload.market_id}")
            platform = payload.metadata.get("platform", "polymarket")
            details_str = json.dumps(payload.metadata, ensure_ascii=False)

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO signals (
                        id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at,
                        target_outcome, estimated_probability, predicted_probability, market_price_at_signal, edge_at_signal, strategy_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type=excluded.type,
                        market_id=excluded.market_id,
                        platform=excluded.platform,
                        edge=excluded.edge,
                        confidence=excluded.confidence,
                        priority=excluded.priority,
                        summary=excluded.summary,
                        details=excluded.details,
                        target_outcome=excluded.target_outcome,
                        estimated_probability=excluded.estimated_probability,
                        predicted_probability=excluded.predicted_probability,
                        market_price_at_signal=excluded.market_price_at_signal,
                        edge_at_signal=excluded.edge_at_signal,
                        strategy_type=excluded.strategy_type
                """, (
                    payload.signal_id,
                    payload.strategy_type.value.upper(),
                    payload.market_id,
                    platform,
                    payload.edge_at_signal,
                    payload.predicted_probability,
                    priority,
                    summary,
                    details_str,
                    "PENDING",
                    payload.created_at.isoformat(),
                    target_outcome,
                    payload.predicted_probability,
                    payload.predicted_probability,
                    payload.market_price_at_signal,
                    payload.edge_at_signal,
                    payload.strategy_type.value
                ))
                conn.commit()
                logger.info(f"Сигнал {payload.signal_id} ({payload.strategy_type.value}) успешно записан.")
        except Exception as e:
            logger.error(f"Ошибка записи сигнала в БД: {e}", exc_info=True)

    def log_resolution(
        self,
        signal_id: str,
        resolution_outcome: str,
        resolution_price: float,
        resolved_at: Optional[datetime] = None
    ) -> None:
        """
        Записывает результат резолюции для сигнала. Идемпотентно.
        """
        try:
            payload = ResolutionPayload(
                signal_id=signal_id,
                resolution_outcome=resolution_outcome,
                resolution_price=resolution_price,
                resolved_at=resolved_at or datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Ошибка валидации данных резолюции: {e}")
            return

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Получаем исходные данные сигнала для вычисления прибыльности
                cursor.execute("""
                    SELECT strategy_type, target_outcome, predicted_probability, market_price_at_signal, details
                    FROM signals WHERE id = ?
                """, (payload.signal_id,))
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Сигнал с ID {payload.signal_id} не найден. Запись резолюции невозможна.")
                    return

                strategy_type = row["strategy_type"]
                target_outcome = row["target_outcome"] or "YES"
                predicted_probability = row["predicted_probability"]
                market_price_at_signal = row["market_price_at_signal"]
                details_str = row["details"]

                metadata = {}
                if details_str:
                    try:
                        metadata = json.loads(details_str)
                    except Exception:
                        pass

                # Вычисляем прибыльность и PnL
                was_profitable, pnl_realized = self._calculate_performance(
                    strategy_type=strategy_type,
                    target_outcome=target_outcome,
                    predicted_probability=predicted_probability,
                    market_price_at_signal=market_price_at_signal,
                    resolution_outcome=payload.resolution_outcome,
                    resolution_price=payload.resolution_price,
                    metadata=metadata
                )

                status = "WIN" if was_profitable else "LOSS"
                if payload.resolution_outcome == "N/A":
                    status = "ARCHIVED"

                cursor.execute("""
                    UPDATE signals SET
                        status = ?,
                        resolved_at = ?,
                        resolution_outcome = ?,
                        resolution_price = ?,
                        was_profitable = ?,
                        pnl_realized = ?
                    WHERE id = ?
                """, (
                    status,
                    payload.resolved_at.isoformat(),
                    payload.resolution_outcome,
                    payload.resolution_price,
                    1 if was_profitable else 0,
                    pnl_realized,
                    payload.signal_id
                ))
                conn.commit()
                logger.info(f"Резолюция для сигнала {payload.signal_id} обновлена: outcome={payload.resolution_outcome}, status={status}, PnL={pnl_realized}")
        except Exception as e:
            logger.error(f"Ошибка записи резолюции в БД: {e}", exc_info=True)

    def _calculate_performance(
        self,
        strategy_type: str,
        target_outcome: str,
        predicted_probability: float,
        market_price_at_signal: float,
        resolution_outcome: str,
        resolution_price: float,
        metadata: Dict[str, Any]
    ) -> tuple[bool, float]:
        """
        Вычисляет, было ли предсказание прибыльным, и считает виртуальный PnL.
        Виртуальная ставка берется из env (по умолчанию $10).
        """
        # Читаем виртуальную ставку из ENV
        import os
        try:
            virtual_stake = float(os.getenv("EVAL_VIRTUAL_STAKE_USD", "10.0"))
        except ValueError:
            virtual_stake = 10.0

        if resolution_outcome == "N/A":
            # Рынок отменен, PnL = 0, не прибыльный
            return False, 0.0

        # По умолчанию (для scout и whale):
        # Если target_outcome совпадает с resolution_outcome
        if strategy_type in ('scout', 'whale') or not strategy_type:
            is_win = (target_outcome == resolution_outcome)
            # Виртуальный PnL
            # Если мы покупаем YES по цене market_price_at_signal, при победе получаем 1.0, иначе 0.0.
            # Если target_outcome == 'NO', мы покупаем NO по цене (1.0 - market_price_at_signal).
            price_safe = market_price_at_signal if market_price_at_signal is not None else 0.5
            buy_price = price_safe if target_outcome == 'YES' else (1.0 - price_safe)
            if buy_price <= 0 or buy_price >= 1:
                buy_price = 0.5  # Защита от деления на 0/нереальных цен

            # Количество контрактов, которые мы могли купить на virtual_stake
            contracts = virtual_stake / buy_price
            if is_win:
                pnl = contracts * (1.0 - buy_price)
            else:
                pnl = -virtual_stake
            return is_win, round(pnl, 2)

        # Для коридоров и арбитража:
        # Для synthetic_corridor и temporal_corridor
        # synthetic_corridor: покупаем YES нижнего рынка и NO верхнего рынка.
        # Обе позиции должны сойтись, чтобы не было пересечения.
        # Если в метаданных записана ожидаемая доходность или логика:
        # Давайте проверим, принес ли спред прибыль.
        # Если resolution_price для обеих сторон совпали с ожиданием.
        # Простой критерий для коридоров: was_profitable = True, если обе стороны верны.
        # А PnL рассчитывается исходя из ROI %, указанного в метаданных, или ROI по исходам.
        # Так как точные цены закрытия индивидуальных рынков коридора могут быть сложными,
        # мы можем использовать сохраненные спреды и ROI в качестве прокси.
        # Если обе стороны в выигрыше (например, нижний YES = 1.0 и верхний NO = 1.0)
        # Давайте по умолчанию считать, что если исход верный, то мы получаем прибыль:
        # was_profitable = True, если обе позиции принесли прибыль.
        # Если в метаданных есть 'roi_min_pct' или 'expected_pnl_pct':
        # Для простоты:
        # Для арбитража (cross_platform): если Polymarket YES == Kalshi YES (или цены сошлись как ожидалось).
        # Давайте сделаем обобщенный расчет:
        # Если в метаданных сохранены детали сторон сделки (например, market_a_outcome, market_b_outcome).
        # Иначе используем простую эвристику: если edge_at_signal > 0 и резолюция совпадает, то профит.
        
        # Давайте по умолчанию для коридоров:
        # was_profitable = True, если мы не вышли за границы (для коридора).
        # В synthetic_corridor в метаданных есть 'pnl_in_corridor_usd' и другие.
        # Если мы не можем детально восстановить, то:
        # Если resolution_outcome == target_outcome:
        is_win = (target_outcome == resolution_outcome)
        pnl = virtual_stake * 0.15 if is_win else -virtual_stake  # Прокси-доходность 15% для выигрышного арбитража/коридора
        return is_win, round(pnl, 2)
