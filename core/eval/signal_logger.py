import json
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from contextlib import contextmanager
from pydantic import BaseModel, Field, field_validator

from agents.shared.python.db import DB_PATH

logger = logging.getLogger("NexusPolyBot.SignalLogger")

class StrategyType(str, Enum):
    SCOUT = 'scout'
    WHALE = 'whale'
    PENNY_STOCKS = 'penny_stocks'

class SignalPayload(BaseModel):
    signal_id: str
    strategy_type: StrategyType
    market_id: str
    predicted_probability: float = Field(..., ge=0.0, le=1.0)
    market_price_at_signal: float = Field(..., ge=0.0, le=1.0)
    edge_at_signal: float = Field(..., ge=-1.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    close_time: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('predicted_probability', 'market_price_at_signal', 'edge_at_signal', mode='before')
    @classmethod
    def validate_signal_floats(cls, v: Any) -> float:
        return float(v)

class ResolutionPayload(BaseModel):
    signal_id: str
    resolution_outcome: str
    resolution_price: float = Field(..., ge=0.0, le=1.0)
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('resolution_price', mode='before')
    @classmethod
    def validate_resolution_floats(cls, v: Any) -> float:
        return float(v)

class SignalLogger:
    """
    Записывает предсказание в момент генерации сигнала и результаты его резолюции.
    Не содержит логики принятия решений.
    """

    @contextmanager
    def _get_connection(self):
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            yield conn

    def log_signal(
        self,
        signal_id: str,
        strategy_type: StrategyType,
        market_id: str,
        predicted_probability: float,
        market_price_at_signal: float,
        edge_at_signal: float,
        metadata: Dict[str, Any],
        created_at: Optional[datetime] = None,
        close_time: Optional[datetime] = None
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
                close_time=close_time or metadata.get("close_time"),
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

            estimated_probability = payload.metadata.get("estimated_probability")
            if estimated_probability is None:
                estimated_probability = payload.predicted_probability
            else:
                try:
                    estimated_probability = float(estimated_probability)
                except (ValueError, TypeError):
                    estimated_probability = payload.predicted_probability

            close_time_str = None
            if payload.close_time:
                ct = payload.close_time
                close_time_str = ct.isoformat() if isinstance(ct, datetime) else str(ct)
            elif "close_time" in payload.metadata:
                raw = payload.metadata["close_time"]
                close_time_str = raw.isoformat() if isinstance(raw, datetime) else str(raw)

            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Архивируем старый PENDING сигнал по этому рынку для той же стратегии, чтобы освободить UNIQUE индекс
                cursor.execute(
                    "UPDATE signals SET status='ARCHIVED' WHERE market_id=? AND status='PENDING' AND id != ? AND strategy_type=?",
                    (payload.market_id, payload.signal_id, payload.strategy_type.value)
                )
                cursor.execute("""
                    INSERT INTO signals (
                        id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at,
                        target_outcome, estimated_probability, predicted_probability, market_price_at_signal, edge_at_signal, strategy_type, close_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        strategy_type=excluded.strategy_type,
                        close_time=excluded.close_time,
                        status = CASE
                            WHEN signals.resolved_at IS NOT NULL THEN signals.status
                            ELSE 'PENDING'
                        END
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
                    estimated_probability,
                    payload.predicted_probability,
                    payload.market_price_at_signal,
                    payload.edge_at_signal,
                    payload.strategy_type.value,
                    close_time_str
                ))
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

                strategy_type = (row["strategy_type"] or "").lower()
                target_outcome = row["target_outcome"] or "YES"
                predicted_probability = row["predicted_probability"]
                market_price_at_signal = row["market_price_at_signal"]
                details_str = row["details"]

                metadata = {}
                if details_str:
                    try:
                        metadata = json.loads(details_str)
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга json details для сигнала {payload.signal_id}: {e}")

                if payload.resolution_outcome == "N/A":
                    status = "ARCHIVED"
                    was_profitable = None
                    pnl_realized = None
                else:
                    # Вычисляем прибыльность и PnL
                    was_profitable_val, pnl_realized = self._calculate_performance(
                        strategy_type=strategy_type,
                        target_outcome=target_outcome,
                        market_price_at_signal=market_price_at_signal,
                        resolution_outcome=payload.resolution_outcome,
                        metadata=metadata
                    )
                    was_profitable = 1 if was_profitable_val else 0
                    status = "WIN" if was_profitable_val else "LOSS"

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
                    was_profitable,
                    pnl_realized,
                    payload.signal_id
                ))
                logger.info(f"Резолюция для сигнала {payload.signal_id} обновлена: outcome={payload.resolution_outcome}, status={status}, PnL={pnl_realized}")
        except Exception as e:
            logger.error(f"Ошибка записи резолюции в БД: {e}", exc_info=True)

    def _calculate_performance(
        self,
        strategy_type: str,
        target_outcome: str,
        market_price_at_signal: float,
        resolution_outcome: str,
        metadata: dict
    ) -> tuple[bool, float]:
        """
        Вычисляет, было ли предсказание прибыльным, и считает виртуальный PnL.
        Виртуальная ставка берется через ConfigProvider (по умолчанию $10).
        """
        strategy_type = (strategy_type or "").lower()
        try:
            from core.config_provider import config_provider
            virtual_stake = float(config_provider.get_sync("eval.virtual_stake_usd", default=10.0))
        except (ValueError, TypeError, ImportError):
            virtual_stake = 10.0

        if resolution_outcome == "N/A":
            # Рынок отменен, PnL = 0, не прибыльный
            return False, 0.0

        is_win = (target_outcome == resolution_outcome)

        # Специальный расчет для Favourite Compounding
        if strategy_type == 'favourite_compound':
            from services.favourite_compounder import calc_compound_pnl
            try:
                from agents.shared.python.db import get_compound_settings
                compound_stake = float(get_compound_settings().get("virtual_stake", virtual_stake))
            except Exception:
                compound_stake = virtual_stake
            price_safe = market_price_at_signal if market_price_at_signal is not None else 0.95
            exit_price = 1.0 if is_win else 0.0
            pnl = calc_compound_pnl(compound_stake, price_safe, exit_price)
            return is_win, pnl

        # Для scout, whale, penny_stocks и по умолчанию
        if strategy_type in ('scout', 'whale', 'penny_stocks') or not strategy_type:
            price_safe = market_price_at_signal if market_price_at_signal is not None else 0.5
            buy_price = price_safe if target_outcome == 'YES' else (1.0 - price_safe)
            if not (0.001 < buy_price < 0.999):
                buy_price = 0.5  # Защита от деления на 0/нереальных цен

            # Количество контрактов, которые мы могли купить на virtual_stake
            contracts = virtual_stake / buy_price
            if is_win:
                # Чистая прибыль с вычетом комиссии Polymarket 2%
                gross_pnl = contracts * (1.0 - buy_price)
                pnl = gross_pnl * 0.98
            else:
                pnl = -virtual_stake
            return is_win, round(pnl, 2)

        # Если мы не можем детально восстановить, то:
        logger.warning(f"Неизвестный strategy_type '{strategy_type}', используется proxy PnL.")
        pnl = (virtual_stake * 0.15 * 0.98) if is_win else -virtual_stake  # Прокси-доходность 15% с вычетом комиссии 2%
        return is_win, round(pnl, 2)
