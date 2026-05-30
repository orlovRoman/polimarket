import pytest
from unittest.mock import patch, MagicMock
from agents.shared.adapters.polymarket import PolymarketAdapter
from datetime import datetime, timezone, timedelta
import json

def _make_market_item(market_id: str):
    return {
        "id": market_id,
        "question": f"Test market {market_id}?",
        "outcomePrices": json.dumps(["0.6", "0.4"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "closed": False,
        "endDate": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
        "slug": f"slug-{market_id}",
        "volumeNum": 5000,
        "clobTokenIds": "[]",
    }

def test_get_event_by_slug_deduplicates(monkeypatch):
    """
    Если один и тот же рынок приходит и в events[], и в markets[] — должен быть 1 экземпляр.
    """
    adapter = PolymarketAdapter()
    
    duplicate_id = "market-abc"
    search_response = {
        "events": [
            {"slug": "event-1", "description": "", "markets": [_make_market_item(duplicate_id)]}
        ],
        "markets": [_make_market_item(duplicate_id)]  # тот же ID
    }
    
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = search_response
    adapter.session.get = MagicMock(return_value=mock_resp)
    
    results = adapter.get_event_by_slug("some-slug")
    ids = [m.id for m in results]
    assert len(ids) == len(set(ids)), f"Найдены дубликаты: {ids}"
