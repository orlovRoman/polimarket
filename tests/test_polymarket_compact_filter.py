import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import json

from agents.shared.adapters.polymarket import PolymarketAdapter

def _make_item(closed=False, end_offset_days=10, has_prices=True):
    end_dt = datetime.now(timezone.utc) + timedelta(days=end_offset_days)
    return {
        "id": f"market-{closed}-{end_offset_days}",
        "question": "Test market?",
        "outcomePrices": json.dumps(["0.6", "0.4"]) if has_prices else "[]",
        "closed": closed,
        "endDate": end_dt.isoformat(),
        "volumeNum": 10000,
        "tags": [],
    }

@pytest.fixture
def adapter():
    return PolymarketAdapter()

def _mock_session_get(items_per_page):
    """Возвращает мок, который отдаёт items_per_page на первой странице, [] на второй."""
    call_count = 0
    def side_effect(url, params=None, timeout=None):
        nonlocal call_count
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if call_count == 0:
            mock_resp.json.return_value = items_per_page
        else:
            mock_resp.json.return_value = []
        call_count += 1
        return mock_resp
    return side_effect

def test_compact_filters_closed_markets(adapter):
    """closed=True рынки не должны попасть в compact-список."""
    items = [
        _make_item(closed=True),
        _make_item(closed=False),
        _make_item(closed="true"),  # строковый вариант
    ]
    adapter.session.get = MagicMock(side_effect=_mock_session_get(items))
    result = adapter.list_all_markets_compact()
    assert len(result) == 1, f"Ожидался 1 рынок (открытый), получено {len(result)}"

def test_compact_filters_expired_markets(adapter):
    """Рынки с датой закрытия в прошлом не должны попасть в compact-список."""
    items = [
        _make_item(end_offset_days=-1),   # истёк вчера
        _make_item(end_offset_days=-100), # давно истёк
        _make_item(end_offset_days=5),    # активный
    ]
    adapter.session.get = MagicMock(side_effect=_mock_session_get(items))
    result = adapter.list_all_markets_compact()
    assert len(result) == 1

def test_compact_allows_no_end_date(adapter):
    """Рынок без endDate — пропускает фильтр по дате (не выбрасывается)."""
    item = _make_item()
    item["endDate"] = ""
    item["end_date_iso"] = None
    item["endDateIso"] = None
    adapter.session.get = MagicMock(side_effect=_mock_session_get([item]))
    result = adapter.list_all_markets_compact()
    assert len(result) == 1, "Рынок без даты должен пройти фильтр"

def test_compact_filters_both_closed_and_expired(adapter):
    """Комбинация closed+expired: оба должны отфильтроваться."""
    items = [
        _make_item(closed=True, end_offset_days=-5),
        _make_item(closed=False, end_offset_days=10),
    ]
    adapter.session.get = MagicMock(side_effect=_mock_session_get(items))
    result = adapter.list_all_markets_compact()
    assert len(result) == 1
