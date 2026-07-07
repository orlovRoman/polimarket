import httpx
import logging
import asyncio
from typing import Optional
from agents.shared.python.db import save_trader_transaction, get_connection

logger = logging.getLogger("NexusPolyBot.DataApiSyncer")

DATA_API_URL = "https://data-api.polymarket.com/trades"

def sync_trades_from_data_api(limit: int = 500):
    """
    Периодически скачивает свежие сделки с открытого Data API Polymarket
    и сохраняет их в базу trader_transactions для последующего анализа Whale Discovery.
    """
    try:
        url = f"{DATA_API_URL}?limit={limit}"
        response = httpx.get(url, timeout=15.0)
        
        if response.status_code != 200:
            logger.error(f"[DataApiSyncer] Ошибка API: HTTP {response.status_code} - {response.text[:200]}")
            return
            
        data = response.json()
        if not data or not isinstance(data, list):
            logger.warning("[DataApiSyncer] Пустой или некорректный ответ от API.")
            return
            
        saved_count = 0
        
        with get_connection() as conn:
            c = conn.cursor()
            
            for trade in data:
                try:
                    wallet = trade.get("proxyWallet")
                    if not wallet:
                        continue
                        
                    # Condition ID — это уникальный идентификатор рынка (исхода) в контрактах
                    market_id = trade.get("conditionId", "")
                    if not market_id:
                        continue
                        
                    outcome = trade.get("outcome", "Unknown")
                    size_shares = float(trade.get("size", 0.0))
                    price = float(trade.get("price", 0.0))
                    
                    # Сумма в USD = кол-во акций * цену покупки
                    amount_usd = size_shares * price
                    if amount_usd <= 0:
                        continue
                        
                    # Для псевдонимов китов (если у кошелька есть имя на Polymarket)
                    alias = trade.get("pseudonym") or trade.get("name")
                    
                    # Проверяем уникальность по времени и кошельку, чтобы не спамить дубликатами.
                    # TransactionHash API к сожалению не выдает для всех сделок стабильно,
                    # но мы можем проверять хэш, если он есть.
                    tx_hash = trade.get("transactionHash")
                    
                    # Так как мы не храним transaction_hash в БД, проверяем по (кошелек, рынок, сумма) за последние 5 минут
                    c.execute("""
                        SELECT 1 FROM trader_transactions 
                        WHERE wallet_address = ? AND market_id = ? AND outcome = ? AND abs(amount_usd - ?) < 0.1
                        AND timestamp >= datetime('now', '-5 minutes')
                        LIMIT 1
                    """, (wallet, market_id, outcome, amount_usd))
                    
                    if c.fetchone():
                        continue  # Уже сохранили недавно
                        
                    # Вызываем оригинальную функцию сохранения (она заодно добавляет кошелек в wallets)
                    save_trader_transaction(
                        wallet_address=wallet,
                        market_id=market_id,
                        outcome=outcome,
                        amount_usd=amount_usd,
                        price=price,
                        alias=alias
                    )
                    saved_count += 1
                except Exception as e:
                    logger.debug(f"[DataApiSyncer] Ошибка парсинга сделки: {e}")
                    
        if saved_count > 0:
            logger.info(f"[DataApiSyncer] Успешно скачано и сохранено новых ончейн-сделок: {saved_count}")
            
    except Exception as e:
        logger.error(f"[DataApiSyncer] Неожиданная ошибка при синхронизации: {e}", exc_info=True)
