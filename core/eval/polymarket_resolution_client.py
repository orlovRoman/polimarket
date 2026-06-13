import logging
from dataclasses import dataclass
from typing import Optional
import requests

logger = logging.getLogger("NexusPolyBot.PolymarketResolutionClient")

@dataclass
class MarketResolution:
    condition_id: str
    is_resolved: bool
    winning_outcome: Optional[str]   # "YES" | "NO" | None (если не разрешён)
    resolution_price: float           # 1.0 = YES win, 0.0 = NO win

class PolymarketResolutionClient:
    BASE_URL = "https://clob.polymarket.com"
    TIMEOUT = 10

    def fetch_resolution(self, condition_id: str) -> Optional[MarketResolution]:
        """
        GET /markets/{condition_id}
        Возвращает None при любой сетевой ошибке (caller логирует).
        """
        if not condition_id:
            logger.warning("[ResolutionClient] Пустой condition_id")
            return None
            
        try:
            resp = requests.get(
                f"{self.BASE_URL}/markets/{condition_id}",
                timeout=self.TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning(f"[ResolutionClient] Ошибка сети при запросе {condition_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"[ResolutionClient] Непредвиденная ошибка при запросе {condition_id}: {e}")
            return None

        # Polymarket возвращает tokens: [{outcome: "YES", price: "1"}, ...]
        tokens = data.get("tokens", [])
        active = data.get("active", True)
        is_resolved = data.get("closed", False) or data.get("resolved", False) or not active

        if not is_resolved:
            return MarketResolution(
                condition_id=condition_id,
                is_resolved=False,
                winning_outcome=None,
                resolution_price=0.0
            )

        # Определяем winner по price == "1" или endDateIso
        winning_outcome = None
        resolution_price = 0.0
        for token in tokens:
            try:
                price = float(token.get("price", 0) or 0)
            except ValueError:
                price = 0.0
            outcome = token.get("outcome", "")
            if price >= 0.99:
                winning_outcome = outcome.upper()   # "YES" или "NO"
                resolution_price = price
                break

        # Если не нашли токен с ценой >= 0.99, но рынок закрыт,
        # возьмем токен с максимальной ценой, если она превышает FALLBACK_WIN_THRESHOLD
        if not winning_outcome and tokens:
            try:
                max_token = max(tokens, key=lambda t: float(t.get("price", 0) or 0))
                max_price = float(max_token.get("price", 0) or 0)
            except (ValueError, TypeError):
                max_price = 0.0
                max_token = None

            FALLBACK_WIN_THRESHOLD = 0.90
            if max_token and max_price >= FALLBACK_WIN_THRESHOLD:
                winning_outcome = max_token.get("outcome", "").upper()
                resolution_price = max_price

        return MarketResolution(
            condition_id=condition_id,
            is_resolved=True,
            winning_outcome=winning_outcome,
            resolution_price=resolution_price
        )
