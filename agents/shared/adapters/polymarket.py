import requests
import json
from datetime import datetime
from typing import List, Optional
from .base_adapter import BaseMarketAdapter
from ..python.models import Market

class PolymarketAdapter(BaseMarketAdapter):
    def __init__(self):
        self.api_url = "https://gamma-api.polymarket.com"

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
            response = requests.get(f"{self.api_url}/events", params=params)
            response.raise_for_status()
            data = response.json()
            
            items = []
            for event in data:
                event_slug = event.get('slug')
                for m in event.get('markets', []):
                    # Добавляем slug события для генерации правильного URL
                    if 'slug' not in m or not m['slug']:
                        m['slug'] = event_slug
                    else:
                        # Polymarket URL часто использует slug события
                        m['event_slug'] = event_slug
                    items.append(m)
        else:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "volume",
                "ascending": "false"
            }
            response = requests.get(f"{self.api_url}/markets", params=params)
            response.raise_for_status()
            items = response.json()
        
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
                
                # Создаем объект Market для основного исхода (обычно YES)
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=f"https://polymarket.com/event/{url_slug}",
                    outcome=outcomes[0],
                    price=float(prices[0]),
                    close_time=datetime.fromisoformat(item["endDate"].replace("Z", "+00:00"))
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
        response = requests.get(f"{self.api_url}/markets", params=params)
        response.raise_for_status()
        return self._parse_markets(response.json(), limit)

    def list_markets_ending_soon(self, limit: int = 20) -> List[Market]:
        """Рынки, закрывающиеся скоро (повышенная волатильность)."""
        params = {
            "active": "true", "closed": "false",
            "limit": limit,
            "order": "endDate", "ascending": "true"
        }
        response = requests.get(f"{self.api_url}/markets", params=params)
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
                
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=q_formatted,
                    description=desc_formatted,
                    url=f"https://polymarket.com/event/{url_slug}",
                    outcome=outcomes[0],
                    price=float(prices[0]),
                    close_time=datetime.fromisoformat(item["endDate"].replace("Z", "+00:00"))
                )
                markets.append(m)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return markets

    def get_market(self, market_id: str) -> Market:
        response = requests.get(f"{self.api_url}/markets/{market_id}")
        response.raise_for_status()
        item = response.json()
        
        outcomes = json.loads(item.get("outcomes", "[]"))
        prices = json.loads(item.get("outcomePrices", "[]"))
        
        # Форматируем заголовок и описание с ценами YES/NO
        q_formatted, desc_formatted = self._format_market_prices(
            item["question"],
            item.get("description"),
            outcomes,
            prices
        )
        
        return Market(
            id=item["id"],
            platform=self.name,
            title=q_formatted,
            description=desc_formatted,
            url=f"https://polymarket.com/event/{item.get('slug')}",
            outcome=outcomes[0],
            price=float(prices[0]),
            close_time=datetime.fromisoformat(item["endDate"].replace("Z", "+00:00"))
        )
