import requests
import json
from datetime import datetime
from typing import List
from .base_adapter import BaseMarketAdapter
from ..python.models import Market

class PolymarketAdapter(BaseMarketAdapter):
    def __init__(self):
        self.api_url = "https://gamma-api.polymarket.com"

    @property
    def name(self) -> str:
        return "polymarket"

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
                
                # Создаем объект Market для основного исхода (обычно YES)
                m = Market(
                    id=item["id"],
                    platform=self.name,
                    title=item["question"],
                    description=item.get("description"),
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

    def get_market(self, market_id: str) -> Market:
        response = requests.get(f"{self.api_url}/markets/{market_id}")
        response.raise_for_status()
        item = response.json()
        
        outcomes = json.loads(item.get("outcomes", "[]"))
        prices = json.loads(item.get("outcomePrices", "[]"))
        
        return Market(
            id=item["id"],
            platform=self.name,
            title=item["question"],
            description=item.get("description"),
            url=f"https://polymarket.com/event/{item.get('slug')}",
            outcome=outcomes[0],
            price=float(prices[0]),
            close_time=datetime.fromisoformat(item["endDate"].replace("Z", "+00:00"))
        )
