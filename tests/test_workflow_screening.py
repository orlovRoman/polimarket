import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

def _compact(id: str, price: float, vol: float = 10000) -> dict:
    return {
        "id": id, "p": price, "vol": vol,
        "end": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
        "q": f"Question {id}"
    }

@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    # list_all_markets_compact возвращает 5 рынков
    adapter.list_all_markets_compact.return_value = [
        _compact("m1", 0.50, 50000),
        _compact("m2", 0.48, 40000),
        _compact("m3", 0.90, 100),    # низкий volume → будет отфильтрован prefilter
        _compact("m4", 0.60, 30000),
        _compact("m5", 0.55, 20000),
    ]
    adapter.get_market.return_value = None  # full objects недоступны → arb_scanner пропускается
    return adapter

def test_screening_no_nexus_llm_called(mock_adapter):
    """NEXUS.screen_markets не вызывается — LLM заменён кодом."""
    from core.workflow import run_screening
    nexus_mock = MagicMock()
    nexus_mock.screen_markets = MagicMock(
        side_effect=AssertionError("NEXUS LLM был вызван!")
    )
    with patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("agents.shared.python.db.get_recently_analyzed_market_ids", return_value=[]), \
         patch("agents.shared.python.db.save_correlation"), \
         patch("core.checkpoint.save_checkpoint"):
        result = run_screening(mock_adapter, category="", market_id="")
    nexus_mock.screen_markets.assert_not_called()
    assert isinstance(result, list)

def test_screening_returns_list_of_ids(mock_adapter):
    """run_screening возвращает список строковых ID."""
    from core.workflow import run_screening
    nexus_mock = MagicMock()
    with patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("agents.shared.python.db.get_recently_analyzed_market_ids", return_value=[]), \
         patch("agents.shared.python.db.save_correlation"), \
         patch("core.checkpoint.save_checkpoint"):
        result = run_screening(mock_adapter, category="", market_id="")
    assert all(isinstance(r, str) for r in result)

def test_screening_uses_cache_on_second_call(mock_adapter):
    """Повторный вызов в пределах SCREENING_INTERVAL_SEC возвращает кеш."""
    from core.workflow import run_screening
    from datetime import datetime, timezone
    import json
    cached_ids = ["m1", "m4", "m5"]
    recent_time = datetime.now(timezone.utc).isoformat()
    memory_store = {
        "last_screen_time": recent_time,
        "screened_market_ids": json.dumps(cached_ids),
    }
    def fake_get_memory(key):
        return memory_store.get(key)
    nexus_mock = MagicMock()
    with patch("core.workflow.get_memory", side_effect=fake_get_memory), \
         patch("config.SCREENING_INTERVAL_SEC", 3600):
        result = run_screening(mock_adapter, category="", market_id="")
    mock_adapter.list_all_markets_compact.assert_not_called()
    assert result == cached_ids
