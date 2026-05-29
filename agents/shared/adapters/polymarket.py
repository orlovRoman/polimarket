import requests
import json
from datetime import datetime
from typing import List, Optional
from .base_adapter import BaseMarketAdapter
from core.models import Market

class PolymarketAdapter(BaseMarketAdapter):
    @staticmethod
    def fetch_raw_events(limit: int = 100) -> list:
        from services.polymarket_cache import get_raw_events
        from config import logger, POLY_EVENTS_CACHE_TTL_SECONDS
        
        def _fetch():
            try:
                resp = requests.get(
                    "https://gamma-api.polymarket.com/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "order": "volume",
                        "ascending": "false",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error(f"[PolymarketAdapter] fetch_raw_events API error: {e}")
                return []
                
        cache_key = f"poly_events_{limit}"
        return get_raw_events(cache_key, _fetch, ttl_seconds=POLY_EVENTS_CACHE_TTL_SECONDS)

    def __init__(self):
        self.api_url = "https://gamma-api.polymarket.com"
        self.session = requests.Session()

    @property
    def name(self) -> str:
        return "polymarket"

    def _format_market_prices(self, question: str, description: Optional[str], outcomes: list, prices: list) -> tuple:
        """
        Форматирует заголовок и описание рынка, добавляя в них стоимость YES и NO контрактов.
        """
        yes_price = None
        no_price = None
        
        try:
            for i, outcome in enumerate(outcomes):
                if i < len(prices):
                    try:
                        p_val = float(prices[i])
                    except (ValueError, TypeError):
                        continue
                    if str(outcome).strip().lower() == 'yes':
                        yes_price = p_val
                    elif str(outcome).strip().lower() == 'no':
                        no_price = p_val
            
            if yes_price is None and no_price is None and len(outcomes) == 2 and len(prices) == 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
        except Exception:
            pass
            
        if yes_price is None and no_price is None:
            if prices:
                try:
                    main_p = float(prices[0])
                except (ValueError, TypeError):
                    main_p = 0.5
                yes_price = main_p
                no_price = 1.0 - main_p
            else:
                yes_price = 0.5
                no_price = 0.5
        elif yes_price is None:
            yes_price = 1.0 - no_price
        elif no_price is None:
            no_price = 1.0 - yes_price
            
        yes_cents = int(round(yes_price * 100))
        no_cents = int(round(no_price * 100))
        
        price_tag = f"YES: {yes_cents}¢ | NO: {no_cents}¢"
        
        formatted_question = question
        if price_tag not in question:
            formatted_question = f"{question} ({price_tag})"
            
        formatted_description = description or ""
        if price_tag not in formatted_description:
            formatted_description = f"[{price_tag}] " + formatted_description
            
        return formatted_question, formatted_description

    
    def _get_end_date(self, item: dict):
        """
        Универсальный парсер даты закрытия.
        Polymarket API возвращает разные имена поля в зависимости от эндпоинта.
        """
        from datetime import datetime, timezone
        for field in ("endDate", "end_date_iso", "endDateIso", "end"):
            raw = item.get(field)
            if raw:
                try:
                    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
        # Fallback: рынок без даты — ставим далёкое будущее, чтобы не фильтровался
        return datetime(2099, 12, 31, tzinfo=timezone.utc)

    def list_markets(self, limit: int = 20, category: str = None) -> List[Market]:
        """Получает список активных рынков с Polymarket. Если передан category, фильтрует по тегу."""
        markets = []
        if category:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "tag_slug": category
            }
            response = self.session.get(f"{self.api_url}/events", params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            items = []
            for event in data:
                event_slug = event.get('slug')
                event_desc = event.get('description', '')
                for m in event.get('markets', []):
                    # Добавляем slug события для генерации правильного URL
                    if 'slug' not in m or not m['slug']:
                        m['slug'] = event_slug
                    else:
                        # Polymarket URL часто использует slug события
                        m['event_slug'] = event_slug
                    if not m.get("description"):
                        m["description"] = event_desc
                    items.append(m)
        else:
            events = self.fetch_raw_events(limit)
            items = []
            for event in events:
                event_slug = event.get('slug')
                event_desc = event.get('description', '')
                for m in event.get('markets', []):
                    if 'slug' not in m or not m['slug']:
                        m['slug'] = event_slug
                    else:
                        m['event_slug'] = event_slug
                    if not m.get("description"):
                        m["description"] = event_desc
                    items.append(m)
        
        for item in items:
            if len(markets) >= limit:
                break
            try:
                # В Polymarket Gamma API outcomes и outcomePrices приходят как строки JSON
                outcomes = json.loads(item.get("outcomes", "[]"))
                prices = json.loads(item.get("outcomePrices", "[]"))
                
                if not outcomes or not prices:
                    continue
                
                # Используем event_slug если есть (из events API), иначе берем slug самого рынка
                url_slug = item.get("event_slug", item.get("slug"))
                
                # Форматируем заголовок и описание с ценами YES/NO
                q_formatted, desc_formatted = self._format_market_prices(
                    item["question"],
                    item.get("description"),
                    outcomes,
                    prices
                )
                
                # Парсим clobTokenIds и volume
                try:
                    tokens = json.loads(item.get("clobTokenIds", "[]")) or None
                except (json.JSONDecodeError, TypeError):
                    tokens = None
                try:
                    volume = float(item.get("volumeNum", 0) or item.get("volume", 0) or 0) or None
                except (ValueError, TypeError):
                    volume = None
                
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=f"https://polymarket.com/event/{url_slug}",
                    outcome=outcomes[0],
                    price=float(prices[0]),
                    close_time=self._get_end_date(item),
                    tokens=tokens,
                    volume=volume,
                    condition_id=item.get("conditionId")
                )
                markets.append(m)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                print(f"Ошибка парсинга рынка {item.get('id')}: {e}")
                continue
                
        return markets

    def list_markets_paged(self, limit: int = 20, offset: int = 0, order: str = "volume") -> List[Market]:
        """Получает рынки с pagination (offset) для ротации."""
        params = {
            "active": "true", "closed": "false",
            "limit": limit, "offset": offset,
            "order": order, "ascending": "false"
        }
        response = self.session.get(f"{self.api_url}/markets", params=params, timeout=15)
        response.raise_for_status()
        return self._parse_markets(response.json(), limit)

    def list_markets_ending_soon(self, limit: int = 20) -> List[Market]:
        """Рынки, закрывающиеся скоро (повышенная волатильность)."""
        params = {
            "active": "true", "closed": "false",
            "limit": limit,
            "order": "endDate", "ascending": "true"
        }
        response = self.session.get(f"{self.api_url}/markets", params=params, timeout=15)
        response.raise_for_status()
        return self._parse_markets(response.json(), limit)

    def _parse_markets(self, items: list, limit: int) -> List[Market]:
        """Общий парсер рынков из ответа API."""
        markets = []
        for item in items:
            if len(markets) >= limit:
                break
            try:
                outcomes = json.loads(item.get("outcomes", "[]"))
                prices = json.loads(item.get("outcomePrices", "[]"))
                if not outcomes or not prices:
                    continue
                url_slug = item.get("event_slug", item.get("slug"))
                
                # Форматируем заголовок и описание с ценами YES/NO
                q_formatted, desc_formatted = self._format_market_prices(
                    item["question"],
                    item.get("description"),
                    outcomes,
                    prices
                )
                
                # Парсим clobTokenIds и volume
                try:
                    tokens = json.loads(item.get("clobTokenIds", "[]")) or None
                except (json.JSONDecodeError, TypeError):
                    tokens = None
                try:
                    volume = float(item.get("volumeNum", 0) or item.get("volume", 0) or 0) or None
                except (ValueError, TypeError):
                    volume = None
                
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=f"https://polymarket.com/event/{url_slug}",
                    outcome=outcomes[0],
                    price=float(prices[0]),
                    close_time=self._get_end_date(item),
                    tokens=tokens,
                    volume=volume,
                )
                markets.append(m)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return markets

    def parse_events_to_markets(self, events: list, limit: int) -> List[Market]:
        """Flatten events to markets and parse them."""
        items = []
        for event in events:
            event_slug = event.get('slug')
            event_desc = event.get('description', '')
            for m in event.get('markets', []):
                if 'slug' not in m or not m['slug']:
                    m['slug'] = event_slug
                else:
                    m['event_slug'] = event_slug
                if not m.get("description"):
                    m["description"] = event_desc
                items.append(m)
        return self._parse_markets(items, limit)

    def get_market(self, market_id: str) -> Optional[Market]:
        response = self.session.get(f"{self.api_url}/markets/{market_id}", timeout=15)
        response.raise_for_status()
        item = response.json()
        
        outcomes = json.loads(item.get("outcomes", "[]"))
        prices = json.loads(item.get("outcomePrices", "[]"))
        
        if not outcomes or not prices:
            return None
        
        # Форматируем заголовок и описание с ценами YES/NO
        q_formatted, desc_formatted = self._format_market_prices(
            item["question"],
            item.get("description"),
            outcomes,
            prices
        )
        
        # Парсим clobTokenIds и volume
        try:
            tokens = json.loads(item.get("clobTokenIds", "[]")) or None
        except (json.JSONDecodeError, TypeError):
            tokens = None
        try:
            volume = float(item.get("volumeNum", 0) or item.get("volume", 0) or 0) or None
        except (ValueError, TypeError):
            volume = None
        
        return Market(
            id=item["id"],
            platform=self.name,
            title=q_formatted,
            description=desc_formatted,
            url=f"https://polymarket.com/event/{item.get('slug')}",
            outcome=outcomes[0],
            price=float(prices[0]),
            close_time=self._get_end_date(item),
            tokens=tokens,
            volume=volume,
        )

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Получает ордербук с CLOB API (без авторизации — read-only)."""
        try:
            resp = self.session.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id},
                timeout=10
            )
            resp.raise_for_status()
            book = resp.json()
            
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            return {
                "bid_depth_5": sum(float(b.get("size", 0)) for b in bids[:5]),
                "ask_depth_5": sum(float(a.get("size", 0)) for a in asks[:5]),
                "spread": round(float(asks[0]["price"]) - float(bids[0]["price"]), 4) if bids and asks else None,
                "top_bid": float(bids[0]["price"]) if bids else None,
                "top_ask": float(asks[0]["price"]) if asks else None,
                "total_bids": len(bids),
                "total_asks": len(asks),
            }
        except Exception as e:
            print(f"Ошибка при получении ордербука для {token_id}: {e}")
            return None

    def list_all_markets_compact(self) -> list:
        """
        Загружает ВСЕ активные рынки для скрининга (compact-формат).
        Пагинированная загрузка по 100 рынков за запрос.
        """
        all_markets = []
        offset = 0
        max_pages = 10  # Защита от бесконечного цикла (макс. 1000 рынков)
        
        while offset < max_pages * 100:
            try:
                params = {
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                    "order": "volume",
                    "ascending": "false"
                }
                resp = self.session.get(f"{self.api_url}/markets", params=params, timeout=15)
                resp.raise_for_status()
                items = resp.json()
                
                if not items:
                    break
                
                for item in items:
                    try:
                        prices = json.loads(item.get("outcomePrices", "[]"))
                        price = float(prices[0]) if prices else 0.5
                        
                        all_markets.append({
                            "id": item.get("id", ""),
                            "q": item.get("question", ""),
                            "p": round(price, 4),
                            "end": item.get("endDate") or item.get("end_date_iso") or item.get("endDateIso") or "",
                            "vol": float(item.get("volumeNum", 0) or item.get("volume", 0) or 0),
                            "tags": item.get("tags", []),
                        })
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                
                offset += 100
                if len(items) < 100:
                    break
                    
            except Exception as e:
                print(f"[PolymarketAdapter] Ошибка при загрузке страницы {offset // 100}: {e}")
                break
        
        print(f"[PolymarketAdapter] Загружено {len(all_markets)} рынков (compact)")
        return all_markets

    def get_event_by_slug(self, slug: str) -> List[Market]:
        """
        Получает рынки по slug события или рынка.
        Сначала пытается найти событие (event) по slug, так как на Polymarket URL обычно ведут на события.
        Если не найдено, пытается найти конкретный рынок по slug.
        """
        markets = []
        # 1. Попытка получить событие
        try:
            resp = self.session.get(f"{self.api_url}/events", params={"slug": slug}, timeout=15)
            resp.raise_for_status()
            events = resp.json()
            if events and isinstance(events, list):
                markets = self.parse_events_to_markets(events, limit=50)
                if markets:
                    return markets
        except Exception as e:
            print(f"[PolymarketAdapter] get_event_by_slug (events step) error: {e}")

        # 2. Попытка получить конкретный рынок, если событие не найдено
        try:
            resp = self.session.get(f"{self.api_url}/markets", params={"slug": slug}, timeout=15)
            resp.raise_for_status()
            raw_markets = resp.json()
            if raw_markets and isinstance(raw_markets, list):
                markets = self._parse_markets(raw_markets, limit=50)
        except Exception as e:
            print(f"[PolymarketAdapter] get_event_by_slug (markets step) error: {e}")

        return markets

    def search_markets(self, query: str, limit: int = 10) -> List[Market]:
        """Ищет активные рынки на Polymarket по ключевому слову/запросу."""
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "query": query
        }
        response = self.session.get(f"{self.api_url}/markets", params=params, timeout=15)
        response.raise_for_status()
        return self._parse_markets(response.json(), limit)
