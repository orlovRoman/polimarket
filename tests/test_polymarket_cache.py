import pytest
import time
from services.polymarket_cache import get_raw_events, invalidate

def test_cache_hit_and_miss():
    invalidate("test_key")
    call_count = 0

    def fetch_mock():
        nonlocal call_count
        call_count += 1
        return [{"id": "1", "markets": []}]

    # MISS
    data1 = get_raw_events("test_key", fetch_mock, ttl_seconds=2)
    assert call_count == 1
    assert len(data1) == 1

    # HIT
    data2 = get_raw_events("test_key", fetch_mock, ttl_seconds=2)
    assert call_count == 1
    assert data1 is data2

    # Expire cache
    time.sleep(2.1)
    data3 = get_raw_events("test_key", fetch_mock, ttl_seconds=2)
    assert call_count == 2
