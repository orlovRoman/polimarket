# tests/test_outcome_tracker_signature.py
import pytest
import inspect
from services.outcome_tracker import _fetch_resolution

def test_fetch_resolution_is_sync_function():
    """_fetch_resolution должна быть синхронной функцией для asyncio.to_thread."""
    assert not inspect.iscoroutinefunction(_fetch_resolution), (
        "_fetch_resolution является async def — нужно заменить "
        "asyncio.to_thread(...) на await _fetch_resolution(...) "
        "в monitor_active_penny_stocks"
    )
