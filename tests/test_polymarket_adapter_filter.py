import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from agents.shared.adapters.polymarket import PolymarketAdapter

def test_parse_markets_filters_closed():
    adapter = PolymarketAdapter()
    
    # Готовим тестовые данные (один активный рынок, один закрытый по флагу, один закрытый по времени)
    now = datetime.now(timezone.utc)
    raw_items = [
        # 1. Активный рынок
        {
            "id": "active_1",
            "question": "Will active work?",
            "outcomes": '["YES", "NO"]',
            "outcomePrices": '[0.5, 0.5]',
            "slug": "active-slug",
            "endDate": (now + timedelta(days=5)).isoformat(),
            "closed": False
        },
        # 2. Закрытый рынок (флаг closed = True)
        {
            "id": "closed_flag",
            "question": "Will closed flag work?",
            "outcomes": '["YES", "NO"]',
            "outcomePrices": '[1.0, 0.0]',
            "slug": "closed-flag-slug",
            "endDate": (now + timedelta(days=5)).isoformat(),
            "closed": True
        },
        # 3. Закрытый рынок (время endDate в прошлом)
        {
            "id": "closed_time",
            "question": "Will closed time work?",
            "outcomes": '["YES", "NO"]',
            "outcomePrices": '[0.0, 1.0]',
            "slug": "closed-time-slug",
            "endDate": (now - timedelta(days=5)).isoformat(),
            "closed": False
        }
    ]
    
    parsed = adapter._parse_markets(raw_items, limit=10)
    
    assert len(parsed) == 1
    assert parsed[0].id == "active_1"
    assert parsed[0].title == "Will active work? (YES: 50¢ | NO: 50¢)"
