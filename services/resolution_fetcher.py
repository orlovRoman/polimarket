import asyncio
import json
import logging
import sqlite3
import httpx
from datetime import datetime, timezone, timedelta

from core.eval.signal_logger import SignalLogger
from agents.shared.python.db import DB_PATH

logger = logging.getLogger("NexusPolyBot.ResolutionFetcher")

class ResolutionFetcher:
    """
    Периодически проверяет закрытые/разрешенные рынки Polymarket,
    записывает исходы в базу данных и обновляет статус сигналов.
    """

    def __init__(self):
        self.signal_logger = SignalLogger()
        self.api_url = "https://gamma-api.polymarket.com"

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    async def fetch_pending_resolutions(self) -> int:
        """
        Находит нерезолвленные сигналы старше 24 часов и опрашивает API Polymarket для получения исхода.
        Возвращает количество обновленных записей.
        """
        logger.info("Запуск ResolutionFetcher для проверки нерезолвленных сигналов...")
        
        # Выбираем сигналы, где resolved_at IS NULL и созданные более 24 часов назад
        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id as signal_id, market_id, strategy_type, platform, target_outcome
                    FROM signals
                    WHERE resolved_at IS NULL
                      AND created_at < ?
                      AND platform IN ('polymarket', 'polymarket_kalshi')
                """, (cutoff_time,))
                pending_signals = cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка при чтении нерезолвленных сигналов из БД: {e}", exc_info=True)
            return 0

        if not pending_signals:
            logger.info("Нет нерезолвленных сигналов для проверки.")
            return 0

        logger.info(f"Найдено {len(pending_signals)} нерезолвленных сигналов. Запускаем опрос API...")
        
        updated_count = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for signal in pending_signals:
                sig_id = signal["signal_id"]
                market_id = signal["market_id"]
                
                # Rate limit: не более 5 запросов в секунду
                await asyncio.sleep(0.25)
                
                resolution_data = await self._get_market_resolution_with_retry(client, market_id)
                if not resolution_data:
                    continue
                
                outcome, final_price, resolved_at = resolution_data
                
                try:
                    # Записываем резолюцию через SignalLogger
                    self.signal_logger.log_resolution(
                        signal_id=sig_id,
                        resolution_outcome=outcome,
                        resolution_price=final_price,
                        resolved_at=resolved_at
                    )
                    
                    # Также обновляем исход в таблице markets
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE markets 
                            SET outcome = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (outcome, market_id))
                        conn.commit()
                    
                    updated_count += 1
                except Exception as e:
                    logger.error(f"Ошибка сохранения резолюции для сигнала {sig_id}: {e}", exc_info=True)

        logger.info(f"ResolutionFetcher завершил работу. Успешно обновлено сигналов: {updated_count}")
        return updated_count

    async def _get_market_resolution_with_retry(
        self, client: httpx.AsyncClient, market_id: str
    ) -> tuple[str, float, datetime] | None:
        """
        Запрашивает статус рынка из Polymarket API с exponential backoff.
        Возвращает (outcome, final_price, resolved_at) или None.
        """
        url = f"{self.api_url}/markets/{market_id}"
        delays = [2.0, 4.0, 8.0]
        
        for attempt, delay in enumerate(delays):
            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    logger.warning(f"Рынок {market_id} не найден (404).")
                    return None
                
                resp.raise_for_status()
                data = resp.json()
                
                closed = data.get("closed", False)
                if not closed:
                    # Рынок еще открыт
                    return None
                
                outcome_prices_str = data.get("outcomePrices")
                if not outcome_prices_str:
                    return None
                    
                try:
                    outcome_prices = json.loads(outcome_prices_str)
                except (json.JSONDecodeError, TypeError):
                    return None
                
                if not outcome_prices:
                    return None
                
                try:
                    winner_index = outcome_prices.index("1")
                except ValueError:
                    # Рынок закрыт, но победитель еще не определен
                    return None
                
                outcome = "YES" if winner_index == 0 else "NO"
                final_price = 1.0
                
                # Пытаемся получить реальное время закрытия/резолюции
                resolved_at_str = data.get("closedTime") or data.get("end_date_iso") or data.get("endDate")
                if resolved_at_str:
                    try:
                        resolved_at = datetime.fromisoformat(resolved_at_str.replace("Z", "+00:00"))
                    except Exception:
                        resolved_at = datetime.now(timezone.utc)
                else:
                    resolved_at = datetime.now(timezone.utc)
                    
                return outcome, final_price, resolved_at
                
            except httpx.HTTPStatusError as e:
                logger.warning(f"Ошибка API (попытка {attempt+1}/{len(delays)}): {e}")
                if attempt < len(delays) - 1:
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при запросе резолюции рынка {market_id}: {e}")
                break
                
        return None
