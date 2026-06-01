from datetime import datetime, timezone
from core.models import Market
from core.event_cluster import cluster_by_event_slug

def test_cluster_groups_by_slug():
    dt = datetime.now(timezone.utc)
    markets = [
        Market(id="1", platform="polymarket", title="GDP > $1T", url="http://x", outcome="YES", price=0.7, close_time=dt, event_slug="us-gdp-2025"),
        Market(id="2", platform="polymarket", title="GDP > $2T", url="http://x", outcome="YES", price=0.8, close_time=dt, event_slug="us-gdp-2025"),
        Market(id="3", platform="polymarket", title="Bitcoin > $100k", url="http://x", outcome="YES", price=0.5, close_time=dt, event_slug="btc-price"),
    ]
    clusters = cluster_by_event_slug(markets)
    assert "us-gdp-2025" in clusters
    assert len(clusters["us-gdp-2025"]) == 2
    assert "btc-price" not in clusters  # одиночный — отброшен

def test_cluster_drops_singletons():
    dt = datetime.now(timezone.utc)
    markets = [
        Market(id="1", platform="polymarket", title="Solo market", url="http://x", outcome="YES", price=0.5, close_time=dt, event_slug="solo")
    ]
    assert cluster_by_event_slug(markets) == {}
