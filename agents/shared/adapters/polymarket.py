import requests
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from .base_adapter import BaseMarketAdapter
from core.models import Market

logger = logging.getLogger("NexusPolyBot.PolymarketAdapter")

def _clean_slug_for_search(slug: str) -> str:
    """Очищает slug от стоп-слов для более надежного текстового поиска в public-search."""
    stopwords = {'will', 'the', 'a', 'an', 'in', 'by', 'to', 'be', 'or', 'and', 'of', 'for'}
    words = [w for w in slug.replace('-', ' ').split() if w.lower() not in stopwords]
    return ' '.join(words[:6])  # берём первые 6 смысловых слов

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

    def _parse_outcome(self, item: dict, outcomes: list) -> str:
        """
        Пытается определить исход закрытого рынка по winner, tokens или outcomePrices.
        Возвращает 'YES', 'NO' или default 'unknown'.
        """
        winner = item.get("winner")
        if winner and str(winner).upper() in ("YES", "NO"):
            return str(winner).upper()
            
        # Пытаемся по tokens
        tokens_list = item.get("tokens", [])
        if tokens_list:
            for t in tokens_list:
                try:
                    if float(t.get("price", 0) or 0) >= 0.99:
                        name = str(t.get("outcome", "")).upper()
                        if name in ("YES", "NO"):
                            return name
                except Exception:
                    continue
                    
        # Пытаемся по outcomePrices
        from services.polymarket_client import parse_outcome_prices
        prices_float = parse_outcome_prices(item.get("outcomePrices"))
        if prices_float:
            try:
                max_idx = prices_float.index(max(prices_float))
                if prices_float[max_idx] >= 0.99:
                    return "YES" if max_idx == 0 else "NO"
            except Exception:
                pass
                
        return 'unknown'
    
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
        """Получает список активных рынков с Polymarket. Если передан category, фильтрует по тегам."""
        
        markets = []
        if category:
            from agents.shared.scan_categories import SCAN_CATEGORIES
            tags_to_query = SCAN_CATEGORIES.get(category, {}).get("tags", [category])
            items = []
            
            for tag in tags_to_query:
                try:
                    params = {
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "tag_slug": tag
                    }
                    response = self.session.get(f"{self.api_url}/events", params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    
                    for event in data:
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
                except Exception as e:
                    logger.debug(f"[PolymarketAdapter] Failed to fetch tag {tag}: {e}")
                    continue
            
            seen = set()
            unique_items = []
            for m in items:
                key = m.get("id") or m.get("conditionId") or f"{m.get('slug','')}::{m.get('question','')}"
                if key not in seen:
                    seen.add(key)
                    unique_items.append(m)
            
            items = unique_items[:limit]
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
                if item.get("closed") is True or item.get("closed") == "true":
                    continue
                close_time = self._get_end_date(item)
                if close_time <= datetime.now(timezone.utc):
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
                
                slug_val = item.get('slug') or item.get('conditionId', '')
                url_val = f"https://polymarket.com/market/{slug_val}" if slug_val else ""
                
                outcome_val = 'unknown'
                if item.get("closed") is True or item.get("closed") == "true":
                    outcome_val = self._parse_outcome(item, outcomes)
                    
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=url_val,
                    outcome=outcome_val,
                    price=float(prices[0]),
                    close_time=close_time,
                    tokens=tokens,
                    volume=volume,
                    condition_id=item.get("conditionId"),
                    event_slug=item.get("event_slug") or item.get("slug")
                )
                markets.append(m)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                logger.error(f"[PolymarketAdapter] list_markets parse error for {item.get('id')}: {e}", exc_info=True)
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
                if item.get("closed") is True or item.get("closed") == "true":
                    continue
                close_time = self._get_end_date(item)
                if close_time <= datetime.now(timezone.utc):
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
                
                slug_val = item.get('slug') or item.get('conditionId', '')
                url_val = f"https://polymarket.com/market/{slug_val}" if slug_val else ""
                
                outcome_val = 'unknown'
                if item.get("closed") is True or item.get("closed") == "true":
                    outcome_val = self._parse_outcome(item, outcomes)
                    
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=url_val,
                    outcome=outcome_val,
                    price=float(prices[0]),
                    close_time=close_time,
                    tokens=tokens,
                    volume=volume,
                    condition_id=item.get("conditionId"),
                    event_slug=item.get("event_slug") or item.get("slug")
                )
                markets.append(m)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return markets

    def parse_events_to_markets(self, events: list, limit: int) -> List[Market]:
        """Flatten events to markets and parse them."""
        items = []
        http_requests_count = 0
        for event in events:
            event_id = event.get('id')
            # Если в событии нет списка markets, догружаем его полную версию по ID
            if event_id and not event.get('markets'):
                if http_requests_count < 5:
                    try:
                        resp = self.session.get(f"{self.api_url}/events/{event_id}", timeout=10)
                        http_requests_count += 1
                        if resp.status_code == 200:
                            event = resp.json()
                    except Exception as e:
                        logger.error(f"[PolymarketAdapter] Failed to load full event by ID {event_id}: {e}")
                else:
                    logger.warning(f"[PolymarketAdapter] Skipping event load for {event_id} due to API request limit (5)")
                    
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
        if item.get("closed") is True or item.get("closed") == "true":
            return None
        close_time = self._get_end_date(item)
        if close_time <= datetime.now(timezone.utc):
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
        
        slug_val = item.get('slug') or item.get('conditionId', '')
        url_val = f"https://polymarket.com/market/{slug_val}" if slug_val else ""
        
        outcome_val = 'unknown'
        if item.get("closed") is True or item.get("closed") == "true":
            outcome_val = self._parse_outcome(item, outcomes)
            
        return Market(
            id=item["id"],
            platform=self.name,
            title=q_formatted,
            description=desc_formatted,
            url=url_val,
            outcome=outcome_val,
            price=float(prices[0]),
            close_time=close_time,
            tokens=tokens,
            volume=volume,
            condition_id=item.get("conditionId"),
            event_slug=item.get("event_slug") or item.get("slug")
        )

    def get_market_tags(self, market_id: str) -> List[str]:
        """Получает теги и слаги для рынка из Gamma API."""
        try:
            response = self.session.get(f"{self.api_url}/markets/{market_id}", timeout=15)
            response.raise_for_status()
            item = response.json()
            tags = item.get("tags", [])
            slug = item.get("slug")
            event_slug = item.get("event_slug")
            
            result = []
            for t in tags:
                if t and isinstance(t, str):
                    result.append(t.lower())
            if slug and isinstance(slug, str):
                result.append(slug.lower())
            if event_slug and isinstance(event_slug, str) and event_slug.lower() not in result:
                result.append(event_slug.lower())
            return list(set(result))
        except Exception as e:
            logger.error(f"[PolymarketAdapter] Ошибка при получении тегов для {market_id}: {e}")
            return []

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
            logger.error(f"[PolymarketAdapter] Error getting orderbook for {token_id}: {e}")
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
                        # Фильтр закрытых
                        if item.get("closed") is True or item.get("closed") == "true":
                            continue
                        
                        # Фильтр истёкших
                        end_raw = item.get("endDate") or item.get("end_date_iso") or item.get("endDateIso") or item.get("end") or ""
                        if end_raw:
                            try:
                                end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                                if end_dt <= datetime.now(timezone.utc):
                                    continue
                            except (ValueError, AttributeError):
                                pass  # без даты — пропускаем фильтр

                        prices = json.loads(item.get("outcomePrices", "[]"))
                        price = float(prices[0]) if prices else 0.5
                        
                        all_markets.append({
                            "id": item.get("id", ""),
                            "q": item.get("question", ""),
                            "p": round(price, 4),
                            "end": end_raw,
                            "vol": float(item.get("volumeNum", 0) or item.get("volume", 0) or 0),
                            "tags": item.get("tags", []),
                        })
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                
                offset += 100
                if len(items) < 100:
                    break
                    
            except Exception as e:
                logger.error(f"[PolymarketAdapter] Ошибка при загрузке страницы {offset // 100}: {e}")
                break
        
        logger.info(f"[PolymarketAdapter] Загружено {len(all_markets)} рынков (compact)")
        return all_markets

    def get_event_by_slug(self, slug: str) -> List[Market]:
        """
        Получает рынки по slug события или рынка.
        Использует /public-search эндпоинт для гибкого текстового поиска по очищенному от стоп-слов slug.
        Если не найдено, делает fallback на точные эндпоинты /events и /markets.
        """
        markets: List[Market] = []
        seen_ids: set[str] = set()
        
        def _dedup(mkts: List[Market]) -> List[Market]:
            result = []
            for m in mkts:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    result.append(m)
            return result

        # 1. Попытка через гибкий /public-search с очищенным запросом
        try:
            cleaned_q = _clean_slug_for_search(slug)
            resp = self.session.get(f"{self.api_url}/public-search", params={"q": cleaned_q}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            # Сначала проверяем события (events)
            events = data.get("events", [])
            if events and isinstance(events, list):
                markets = _dedup(self.parse_events_to_markets(events, limit=50))
                if markets:
                    return markets
                    
            # Затем проверяем рынки (markets)
            raw_markets = data.get("markets", [])
            if raw_markets and isinstance(raw_markets, list):
                markets = _dedup(self._parse_markets(raw_markets, limit=50))
                if markets:
                    return markets
        except Exception as e:
            logger.error(f"[PolymarketAdapter] get_event_by_slug (public-search step) error: {e}")

        # 2. Fallback: точное совпадение по /events?slug=
        try:
            resp = self.session.get(f"{self.api_url}/events", params={"slug": slug}, timeout=15)
            resp.raise_for_status()
            events = resp.json()
            if events and isinstance(events, list):
                markets = _dedup(self.parse_events_to_markets(events, limit=50))
                if markets:
                    return markets
        except Exception as e:
            logger.error(f"[PolymarketAdapter] get_event_by_slug (fallback events step) error: {e}")

        # 3. Fallback: точное совпадение по /markets?slug=
        try:
            resp = self.session.get(f"{self.api_url}/markets", params={"slug": slug}, timeout=15)
            resp.raise_for_status()
            raw_markets = resp.json()
            if raw_markets and isinstance(raw_markets, list):
                markets = _dedup(self._parse_markets(raw_markets, limit=50))
        except Exception as e:
            logger.error(f"[PolymarketAdapter] get_event_by_slug (fallback markets step) error: {e}")

        return markets

    def search_markets(self, query: str, limit: int = 10) -> List[Market]:
        """Ищет активные рынки на Polymarket по ключевому слову/запросу с помощью /public-search."""
        # 1. Попытка через гибкий /public-search
        try:
            resp = self.session.get(f"{self.api_url}/public-search", params={"q": query}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            markets = []
            # Собираем рынки из найденных событий
            events = data.get("events", [])
            if events and isinstance(events, list):
                markets.extend(self.parse_events_to_markets(events, limit=limit))
                
            # Добавляем из секции markets
            raw_markets = data.get("markets", [])
            if raw_markets and isinstance(raw_markets, list):
                markets.extend(self._parse_markets(raw_markets, limit=limit))
                
            # Удаляем дубликаты по id
            unique_markets = []
            seen_ids = set()
            for m in markets:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    unique_markets.append(m)
                    
            if unique_markets:
                return unique_markets[:limit]
        except Exception as e:
            logger.error(f"[PolymarketAdapter] search_markets (public-search) error: {e}")

        # Fallback на оригинальный /markets?query=
        try:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "query": query
            }
            response = self.session.get(f"{self.api_url}/markets", params=params, timeout=15)
            response.raise_for_status()
            return self._parse_markets(response.json(), limit)
        except Exception as e:
            logger.error(f"[PolymarketAdapter] search_markets (fallback) error: {e}")
            return []
