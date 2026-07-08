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
        
        for trade in data:
            try:
                wallet = trade.get("proxyWallet")
                if not wallet:
                    continue
                    
                market_id = trade.get("conditionId", "")
                if not market_id:
                    continue
                    
                outcome = trade.get("outcome", "Unknown")
                size_shares = float(trade.get("size", 0.0))
                price = float(trade.get("price", 0.0))
                amount_usd = size_shares * price
                if amount_usd <= 0:
                    continue
                    
                alias = trade.get("pseudonym") or trade.get("name")
                
                tx_hash = trade.get("transactionHash")
                
                # Короткое соединение для проверки дублей и создания рынка
                skip_trade = False
                with get_connection() as conn:
                    c = conn.cursor()
                    
                    if tx_hash:
                        c.execute("SELECT 1 FROM trader_transactions WHERE tx_hash = ? LIMIT 1", (tx_hash,))
                        if c.fetchone():
                            skip_trade = True
                    else:
                        c.execute("""
                            SELECT 1 FROM trader_transactions 
                            WHERE wallet_address = ? AND market_id = ? AND outcome = ? AND abs(amount_usd - ?) < 0.1
                            AND timestamp >= datetime('now', '-1 hour')
                            LIMIT 1
                        """, (wallet, market_id, outcome, amount_usd))
                        if c.fetchone():
                            skip_trade = True
                    
                    if not skip_trade:
                        title = trade.get("title", f"Unknown Market {market_id}")
                        slug = trade.get("slug", "")
                        url = f"https://polymarket.com/event/{slug}" if slug else ""
                        c.execute("""
                            INSERT OR IGNORE INTO markets (id, platform, title, url, outcome, price, close_time, condition_id, volume)
                            VALUES (?, 'polymarket', ?, ?, 'unknown', ?, datetime('now', '+1 year'), ?, 500000000.0)
                        """, (market_id, title, url, price, market_id))
                        
                if skip_trade:
                    continue
                    
                # Сохраняем саму транзакцию (внутри откроется новое соединение)
                save_trader_transaction(
                    wallet_address=wallet,
                    market_id=market_id,
                    outcome=outcome,
                    amount_usd=amount_usd,
                    price=price,
                    alias=alias,
                    tx_hash=tx_hash
                )
                saved_count += 1
            except Exception as e:
                logger.debug(f"[DataApiSyncer] Ошибка парсинга сделки: {e}")
                    
        if saved_count > 0:
            logger.info(f"[DataApiSyncer] Успешно скачано и сохранено новых ончейн-сделок: {saved_count}")
            
    except Exception as e:
        logger.error(f"[DataApiSyncer] Неожиданная ошибка при синхронизации: {e}", exc_info=True)
