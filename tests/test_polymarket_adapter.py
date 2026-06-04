import pytest
from unittest.mock import MagicMock
from agents.shared.adapters.polymarket import PolymarketAdapter

class MockSession:
    def __init__(self):
        self.responses = {}

    def get(self, url, params=None, timeout=None):
        tag = params.get("tag_slug") if params else None
        data = self.responses.get(tag, [])
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

@pytest.fixture
def mock_adapter():
    adapter = PolymarketAdapter()
    adapter.session = MockSession()
    def set_tag_response(tag, data):
        adapter.session.responses[tag] = data
    adapter.set_tag_response = set_tag_response
    return adapter

def test_list_markets_deduplication(mock_adapter):
    """Один рынок по двум тегам — в результате должен быть один экземпляр."""
    market = {
        "id": "mkt1", 
        "question": "Will X?", 
        "slug": "will-x",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]'
    }
    mock_adapter.set_tag_response("politics", [{"slug": "evt1", "markets": [market]}])
    mock_adapter.set_tag_response("elections", [{"slug": "evt1", "markets": [market]}])
    result = mock_adapter.list_markets(limit=20, category="politics")
    ids = [m.id for m in result]
    assert ids.count("mkt1") == 1

def test_list_markets_respects_limit(mock_adapter):
    """list_markets(limit=5) не должен возвращать больше 5 элементов."""
    markets = [{
        "id": f"mkt{i}", 
        "slug": f"mkt-{i}", 
        "question": f"Q{i}",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]'
    } for i in range(30)]
    mock_adapter.set_tag_response("politics", [{"slug": "e", "markets": markets}])
    result = mock_adapter.list_markets(limit=5, category="politics")
    assert len(result) <= 5
