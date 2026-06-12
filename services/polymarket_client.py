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

def parse_outcome_prices(raw_prices) -> list[float]:
    """Парсит outcomePrices из строки или списка в список float."""
    if not raw_prices:
        return []
    try:
        if isinstance(raw_prices, str):
            prices_list = json.loads(raw_prices)
        else:
            prices_list = raw_prices
        if prices_list:
            return [float(p) for p in prices_list]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []

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

        # 2. Проверяем через tokens (YES/NO токены с ценой >= 0.99)
        tokens = data.get("tokens", [])
        if tokens:
            for token in tokens:
                try:
                    price_val = float(token.get("price", 0) or 0)
                    if price_val >= 0.99:
                        outcome_name = str(token.get("outcome", "")).upper()
                        if outcome_name in ("YES", "NO"):
                            return outcome_name
                except (ValueError, TypeError):
                    continue

        # 3. Fallback: проверяем closed + outcomePrices
        closed = data.get("closed", False)
        if not closed:
            return None

        prices_float = parse_outcome_prices(data.get("outcomePrices"))
        if prices_float:
            winner_index = next(
                (i for i, p in enumerate(prices_float) if p >= 0.99),
                None
            )
            if winner_index is not None:
                return "YES" if winner_index == 0 else "NO"
                
        return None
    except Exception as e:
        logger.error(f"[PolymarketClient] Ошибка при получении резолюции для {market_id}: {e}")
        return None
