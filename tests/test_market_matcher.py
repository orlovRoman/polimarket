import pytest
from datetime import datetime, timezone
from core.models import Market
from services.market_matcher import find_candidate_pairs

def make_market(title, price, platform="polymarket", mid=None):
    return Market(
        id=mid or title[:10], platform=platform, title=title,
        url="https://polymarket.com/test", outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

def test_same_platform_false_positive_filtered():
    markets_a = [make_market("SpaceX IPO above $3T", 0.12, mid="a1")]
    markets_b = [make_market("SpaceX IPO above $1.8T", 0.84, mid="b1")]
    pairs = find_candidate_pairs(markets_a, markets_b, min_score=0.3)
    assert len(pairs) == 0

def test_same_platform_real_violation_passes():
    markets_a = [make_market("SpaceX IPO above $3T", 0.90, mid="a1")]
    markets_b = [make_market("SpaceX IPO above $1.8T", 0.60, mid="b1")]
    pairs = find_candidate_pairs(markets_a, markets_b, min_score=0.3)
    assert len(pairs) == 1

def test_cross_platform_always_passes():
    markets_a = [make_market("Will Fed cut rates in June?", 0.50, "polymarket", "a1")]
    markets_b = [make_market("Will Fed cut rates in June?", 0.53, "kalshi", "b1")]
    pairs = find_candidate_pairs(markets_a, markets_b, min_score=0.3)
    assert len(pairs) == 1
