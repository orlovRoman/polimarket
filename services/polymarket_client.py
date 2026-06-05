# services/polymarket_client.py
"""
Клиент для взаимодействия с Polymarket API.
Предоставляет функции для получения результатов закрытых рынков.
"""
import requests
import json
import logging
from typing import Optional

logger = logging.getLogger("NexusPolyBot.PolymarketClient")

def get_market_resolution(market_id: str) -> Optional[str]:
    """
    Запрашивает статус рынка из Polymarket API.
    Возвращает 'YES', 'NO' или None, если рынок не закрыт/не разрешен.
    """
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            logger.warning(f"[PolymarketClient] Рынок {market_id} не найден (404)")
            return None
        resp.raise_for_status()
        data = resp.json()
        
        # 1. Проверяем поле winner (YES/NO)
        winner = data.get("winner")
        if winner and str(winner).upper() in ("YES", "NO"):
            return str(winner).upper()
            
        # 2. Fallback: проверяем closed + outcomePrices
        closed = data.get("closed", False)
        if not closed:
            return None
            
        outcome_prices_str = data.get("outcomePrices")
        if outcome_prices_str:
            try:
                outcome_prices = json.loads(outcome_prices_str)
                if outcome_prices:
                    winner_index = outcome_prices.index("1")
                    return "YES" if winner_index == 0 else "NO"
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
                
        return None
    except Exception as e:
        logger.error(f"[PolymarketClient] Ошибка при получении резолюции для {market_id}: {e}")
        return None
