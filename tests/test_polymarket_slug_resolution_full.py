"""
Тест для проверки извлечения рынков по slug события с догрузкой по ID.
Убеждаемся, что при указании slug события возвращаются все вложенные рынки.
"""
import pytest
import asyncio
from agents.shared.adapters.polymarket import PolymarketAdapter
from services.telegram_listener import resolve_market_ids_from_url

def test_get_event_by_slug_fetches_markets_successfully():
    adapter = PolymarketAdapter()
    
    # kraken-ipo-in-2025 — это 100% активный slug события
    slug = "kraken-ipo-in-2025"
    
    markets = adapter.get_event_by_slug(slug)
    
    assert len(markets) > 0, "Не найдено ни одного рынка для Kraken IPO!"
    
    # Проверяем, что среди найденных рынков есть правильные вопросы
    titles = [m.title.lower() for m in markets]
    assert any("kraken" in t or "ipo" in t for t in titles), f"Неверные рынки: {titles}"

def test_resolve_market_ids_from_url_with_event_slug():
    url = "https://polymarket.com/event/kraken-ipo-in-2025"
    
    market_ids = asyncio.run(resolve_market_ids_from_url(url))
    
    assert len(market_ids) > 0, "Не удалось извлечь ID рынков из URL события!"
