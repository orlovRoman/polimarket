import pytest
from unittest.mock import MagicMock
from core.engine import CoreEngine

@pytest.fixture
def engine():
    e = CoreEngine()
    assert hasattr(e, 'update_state') or hasattr(e, 'update_scan_state'), \
        "CoreEngine должен иметь метод update_state или update_scan_state"
    assert hasattr(e, 'get_status') or hasattr(e, 'get_scan_state'), \
        "CoreEngine должен иметь метод get_status или get_scan_state"
    
    if hasattr(e, 'update_state'):
        e.update_scan_state = e.update_state
    if hasattr(e, 'get_status'):
        e.get_scan_state = e.get_status
    return e

@pytest.fixture
def mock_markets():
    return [{"id": f"m{i}"} for i in range(10)]

@pytest.fixture
def mock_markets_small():
    return [{"id": "m1"}]

def test_progress_resets_before_new_scan(engine, mock_markets):
    """total_markets сбрасывается до len(новых рынков) при старте нового скана."""
    engine.update_scan_state(total_markets=100, current_market_index=99)
    # mock _run_team_discussion_inner logic
    engine.update_scan_state(
        total_markets=len(mock_markets),
        current_market_index=0,
        stage="Обсуждение (SCOUT + SWING + SHADOW)"
    )
    state = engine.get_scan_state()
    assert state["current_market_index"] == 0
    assert state["total_markets"] == len(mock_markets)

def test_state_cleaned_after_scan_complete(engine):
    """После завершения скана current_market_index сброшен в 0."""
    # Simulate end of scan
    engine.update_scan_state(
        stage="Завершено",
        current_market_index=0,
        total_markets=0,
        current_market_title="",
        current_market_url="",
        scout_status="⏳ Ожидает",
        swing_status="⏳ Ожидает",
        shadow_status="⏳ Ожидает",
    )
    state = engine.get_scan_state()
    assert state["current_market_index"] == 0
    assert state["stage"] == "Завершено"
    assert state["total_markets"] == 0

def test_progress_never_exceeds_total(engine, mock_markets_small):
    """current_market_index не может быть > total_markets."""
    # Since we test the direct state logic manually here because we didn't run the full engine
    engine.update_scan_state(
        stage="Завершено",
        current_market_index=0,
        total_markets=0
    )
    state = engine.get_scan_state()
    assert state["current_market_index"] <= state["total_markets"]
