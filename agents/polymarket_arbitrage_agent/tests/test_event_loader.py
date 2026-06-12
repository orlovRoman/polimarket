import pytest
from datetime import datetime, timezone
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw

def test_load_events_with_levels_from_raw():
    raw_event = {
        "slug": "btc-price",
        "title": "BTC Price",
        "markets": [
            {"id": "1", "question": "BTC above $100K", "outcomePrices": '["0.8", "0.2"]', "volume": "5000"},
            {"id": "2", "question": "BTC above $150K", "outcomePrices": '["0.5", "0.5"]', "volume": "6000"},
        ]
    }
    events = load_events_with_levels_from_raw([raw_event], min_markets_per_event=2, min_volume_per_market=1000)
    assert len(events) == 1
    assert events[0].event_slug == "btc-price"
    assert len(events[0].markets) == 2
