import requests
from datetime import datetime, timezone
from typing import Optional, List
from .base_adapter import BaseMarketAdapter
from core.models import Market

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiAdapter(BaseMarketAdapter):
    """
    Read-only адаптер для Kalshi.
    Не требует OAuth — работает только с публичными данными.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PolymarketBot/1.0"
        })

    @property
    def name(self) -> str:
        return "kalshi"

    def list_markets(self, limit: int = 100, status: str = "open") -> List[Market]:
        """Получает список активных рынков без авторизации."""
        cursor = None
        markets = []
        import time
        while len(markets) < limit:
            params = {
                "status": status,
                "limit": min(100, limit - len(markets))
            }
            if cursor:
                params["cursor"] = cursor
            
            success = False
            for attempt in range(3):
                try:
                    resp = self.session.get(f"{KALSHI_API}/markets", params=params, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                    success = True
                    break
                except requests.exceptions.Timeout:
                    print(f"[KalshiAdapter] Timeout, попытка {attempt+1}/3")
                    time.sleep(2)
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code
                    if 500 <= status_code < 600:
                        print(f"[KalshiAdapter] 5xx Error, попытка {attempt+1}/3")
                        time.sleep(3 * (attempt + 1))
                    else:
                        print(f"[KalshiAdapter] 4xx Error: {e}")
                        break
                except Exception as e:
                    print(f"[KalshiAdapter] Ошибка list_markets: {e}")
                    break
            
            if not success:
                print("[KalshiAdapter] Не удалось загрузить рынки после нескольких попыток.")
                break

            for item in data.get("markets", []):
                m = self._parse(item)
                if m:
                    markets.append(m)

            cursor = data.get("cursor")
            if not cursor:
                break

        return markets

    def get_market(self, ticker: str) -> Optional[Market]:
        """Получает конкретный рынок по тикеру."""
        try:
            resp = self.session.get(f"{KALSHI_API}/markets/{ticker}", timeout=15)
            resp.raise_for_status()
            return self._parse(resp.json().get("market", {}))
        except Exception as e:
            print(f"[KalshiAdapter] Ошибка get_market({ticker}): {e}")
            return None

    def get_orderbook(self, ticker: str) -> dict:
        """Получает стакан ордеров (публичный эндпоинт)."""
        try:
            resp = self.session.get(
                f"{KALSHI_API}/markets/{ticker}/orderbook", timeout=10
            )
            resp.raise_for_status()
            book = resp.json().get("orderbook", {})

            yes_bids = book.get("yes", [])   # [[price_cents, size], ...]
            no_bids  = book.get("no", [])    # NO side = implied YES asks

            top_bid = yes_bids[0][0] / 100 if yes_bids else None
            top_ask = (100 - no_bids[0][0]) / 100 if no_bids else None
            spread  = round(top_ask - top_bid, 4) if top_bid and top_ask else None

            return {
                "top_bid":     top_bid,
                "top_ask":     top_ask,
                "spread":      spread,
                "bid_depth_5": sum(b[1] for b in yes_bids[:5]),
                "ask_depth_5": sum(a[1] for a in no_bids[:5]),
            }
        except Exception as e:
            print(f"[KalshiAdapter] Ошибка get_orderbook({ticker}): {e}")
            return {}

    def list_markets_by_series(self, series_ticker: str, limit: int = 50) -> List[Market]:
        """Получает рынки конкретной серии (категории)."""
        try:
            params = {"series_ticker": series_ticker, "status": "open", "limit": limit}
            resp = self.session.get(f"{KALSHI_API}/markets", params=params, timeout=15)
            resp.raise_for_status()
            return [m for item in resp.json().get("markets", []) if (m := self._parse(item))]
        except Exception as e:
            print(f"[KalshiAdapter] Ошибка list_markets_by_series({series_ticker}): {e}")
            return []

    def _parse(self, item: dict) -> Optional[Market]:
        """Конвертирует словарь Kalshi API в объект Market."""
        ticker = item.get("ticker")
        if not ticker:
            return None

        # Цена YES: mid между лучшим бидом и аском
        yes_bid = item.get("yes_bid")
        yes_ask = item.get("yes_ask")
        if yes_bid is not None and yes_ask is not None:
            price = round((yes_bid + yes_ask) / 2 / 100, 4)
        elif yes_ask is not None:
            price = round(yes_ask / 100, 4)
        elif yes_bid is not None:
            price = round(yes_bid / 100, 4)
        else:
            return None  # Нет данных о цене

        # Время закрытия
        close_str = item.get("close_time") or item.get("expiration_time")
        if not close_str:
            return None
        try:
            close_time = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        except Exception:
            return None

        # Генерируем URL
        url = f"https://kalshi.com/markets/{ticker}"

        return Market(
            id=ticker,
            platform="kalshi",
            title=item.get("title", ticker),
            description=item.get("rules_primary", ""),
            url=url,
            outcome="YES",
            price=price,
            close_time=close_time,
            volume=item.get("volume"),
        )
