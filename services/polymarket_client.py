# services/polymarket_client.py
"""
Клиент для взаимодействия с Polymarket API.
Предоставляет функции для получения результатов закрытых рынков.
"""
import requests
import json
import logging
from typing import Optional
from datetime import datetime, timezone

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

def _is_negrisk_resolved(data: dict, market_id: str) -> Optional[str]:
    """
    Специальная проверка для negRisk-рынков (tokens: []).
    Такие рынки часто долго остаются closed=false даже после реального завершения.
    Считаем рынок разрешённым, если выполнены ВСЕ три условия:
      1. tokens пуст (признак negRisk-формата)
      2. endDate уже прошёл (минимум на 1 час)
      3. outcomePrices показывает явного победителя с уверенностью >= 99.9%
    """
    tokens = data.get("tokens", [])
    if tokens:
        # Это обычный рынок с токенами — данная функция не применяется
        return None

    end_date_str = data.get("endDate")
    if not end_date_str:
        return None

    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        # Даём минимум 1 час после endDate для корректного обновления API
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        if now < end_date:
            return None
        hours_since_end = (now - end_date).total_seconds() / 3600
        if hours_since_end < 1.0:
            return None
    except (ValueError, TypeError):
        return None

    # Проверяем outcomePrices с очень высоким порогом (0.999 = 99.9%)
    prices_float = parse_outcome_prices(data.get("outcomePrices"))
    if not prices_float:
        return None

    winner_index = next(
        (i for i, p in enumerate(prices_float) if p >= 0.999),
        None
    )
    if winner_index is not None:
        outcome = "YES" if winner_index == 0 else "NO"
        logger.info(
            f"[PolymarketClient] negRisk рынок {market_id}: closed=false, "
            f"но endDate {end_date_str} прошёл и outcomePrices={prices_float} → {outcome}"
        )
        return outcome

    return None

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
        
        # 1. Проверяем, закрыт ли рынок.
        closed = data.get("closed", False)
        if not closed:
            # Исключение: negRisk-рынки (tokens=[]) часто зависают в closed=false
            # даже после реального завершения — обрабатываем их отдельно.
            negrisk_result = _is_negrisk_resolved(data, market_id)
            if negrisk_result:
                # UMA-проверка для negRisk
                uma_status = data.get("umaResolutionStatus")
                if uma_status and str(uma_status).lower() != "resolved":
                    return None
                return negrisk_result
            return None
            
        # Проверяем статус UMA-резолюции, если он есть.
        # Если статус UMA не равен 'resolved', рынок еще находится в процессе (proposed/disputed),
        # и доверять текущим исходам или ценам нельзя.
        uma_status = data.get("umaResolutionStatus")
        if uma_status and str(uma_status).lower() != "resolved":
            logger.info(f"[PolymarketClient] Рынок {market_id} закрыт, но статус UMA '{uma_status}' не равен 'resolved'. Пропускаем.")
            return None
            
        # 2. Проверяем поле winner (YES/NO)
        winner = data.get("winner")
        if winner and str(winner).upper() in ("YES", "NO"):
            return str(winner).upper()

        # 3. Проверяем через tokens (YES/NO токены с ценой >= 0.99)
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

        # 4. Fallback: проверяем outcomePrices
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

