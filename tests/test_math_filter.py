import pytest
from datetime import datetime, timezone
from core.models import Market
from core.math_filter import math_pre_filter, FilterDecision, MathFilterResult, validate_trade_instruction

def make_market(title: str, price: float, platform: str = "polymarket",
                url: str = "https://polymarket.com/test") -> Market:
    return Market(
        id="test", platform=platform, title=title, url=url,
        outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

def test_math_filter_imports_without_error():
    import core.math_filter
    assert hasattr(core.math_filter, 'validate_trade_instruction')

def test_spacex_bug():
    market_a = make_market("SpaceX IPO above $3T", 0.12)
    market_b = make_market("SpaceX IPO above $1.8T", 0.84)
    result = math_pre_filter(market_a, market_b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.has_arbitrage is False

def test_monotonicity_violation():
    market_a = make_market("SpaceX IPO above $3T", 0.90)
    market_b = make_market("SpaceX IPO above $1.8T", 0.60)
    result = math_pre_filter(market_a, market_b)
    assert result.decision == FilterDecision.CONFIRMED_ARBITRAGE
    assert result.arbitrage_type == "monotonicity_violation"
    assert result.spread_pct == pytest.approx(30.0, abs=0.1)
    assert result.has_arbitrage is True
    assert "BUY" in result.trade_instruction
    assert "SELL" not in result.trade_instruction.upper()

def test_same_thresholds_different_platforms():
    a = make_market("SpaceX IPO above $3T", 0.12, platform="polymarket")
    b = make_market("SpaceX IPO above $3T", 0.22, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.arbitrage_type != "monotonicity_violation"

def test_percentages_monotonicity_kept():
    a = make_market("Will unemployment exceed 5%?", 0.30)
    b = make_market("Will unemployment exceed 4%?", 0.80)
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI

def test_complementary_overpriced():
    a = make_market("Will Democrats win the Senate?", 0.60)
    b = make_market("Will Republicans win the Senate?", 0.55)
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_ARBITRAGE
    assert result.arbitrage_type == "complementary_overpriced"
    assert result.spread_pct == pytest.approx(15.0, abs=0.5)
    assert result.has_arbitrage is True
    assert "BUY NO" in result.trade_instruction
    assert "SELL" not in result.trade_instruction.upper()

def test_cross_platform_small_spread():
    a = make_market("Will Fed cut rates in June?", 0.50, platform="polymarket")
    b = make_market("Will Fed cut rates in June?", 0.53, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI

def test_cross_platform_big_spread():
    a = make_market("Will Fed cut rates in June?", 0.40, platform="polymarket")
    b = make_market("Will Fed cut rates in June?", 0.60, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.AMBIGUOUS
    assert result.spread_pct == pytest.approx(20.0, abs=0.1)

def test_return_dataclass():
    a = make_market("Some market", 0.5)
    b = make_market("Other market", 0.5)
    result = math_pre_filter(a, b)
    assert isinstance(result, MathFilterResult)
    assert hasattr(result, 'decision')
    assert hasattr(result, 'spread_pct')
    assert hasattr(result, 'trade_instruction')

def test_validate_trade_instruction_rejects_sell_yes():
    is_valid, reason = validate_trade_instruction("SELL YES на рынке A + BUY NO на рынке B")
    assert is_valid is False
    assert "SELL YES" in reason

def test_validate_trade_instruction_rejects_sell_no():
    is_valid, reason = validate_trade_instruction("sell no на рынке X")
    assert is_valid is False

def test_validate_trade_instruction_allows_buy():
    is_valid, _ = validate_trade_instruction("BUY YES на рынке A + BUY NO на рынке B")
    assert is_valid is True

def test_logical_implication_returns_ambiguous_not_arbitrage():
    """logical_implication не должен быть CONFIRMED_ARBITRAGE и has_arbitrage=False"""
    from core.models import Market
    from datetime import datetime, timedelta, timezone
    
    m_a = Market(id="a", platform="polymarket", title="Russia Ukraine ceasefire by Oct 2026",
                 description="", url="https://polymarket.com/a", outcome="YES",
                 price=0.32, close_time=datetime.now(timezone.utc) + timedelta(days=90))
    m_b = Market(id="b", platform="polymarket", title="Ukraine limits armed forces before 2027",
                 description="", url="https://polymarket.com/b", outcome="YES",
                 price=0.18, close_time=datetime.now(timezone.utc) + timedelta(days=180))
    
    result = math_pre_filter(m_a, m_b)
    
    assert result.decision != FilterDecision.CONFIRMED_ARBITRAGE
    assert result.has_arbitrage is False
    assert "SELL" not in result.trade_instruction.upper()

def test_logical_implication_not_triggered_by_default():
    """Без флага два несвязанных рынка Polymarket не должны получать logical_implication"""
    from core.models import Market
    from datetime import datetime, timedelta, timezone
    
    m_a = Market(id="a", platform="polymarket", title="Russia Ukraine ceasefire by Oct 2026",
                 description="", url="https://polymarket.com/a", outcome="YES",
                 price=0.32, close_time=datetime.now(timezone.utc) + timedelta(days=90))
    m_b = Market(id="b", platform="polymarket", title="Ukraine limits armed forces before 2027",
                 description="", url="https://polymarket.com/b", outcome="YES",
                 price=0.18, close_time=datetime.now(timezone.utc) + timedelta(days=180))
                 
    result = math_pre_filter(m_a, m_b)  # флаг не передан
    assert result.arbitrage_type != "logical_implication"

def test_logical_implication_triggered_with_flag():
    from core.models import Market
    from datetime import datetime, timedelta, timezone
    
    m_a = Market(id="a", platform="polymarket", title="Russia Ukraine ceasefire by Oct 2026",
                 description="", url="https://polymarket.com/a", outcome="YES",
                 price=0.32, close_time=datetime.now(timezone.utc) + timedelta(days=90))
    m_b = Market(id="b", platform="polymarket", title="Ukraine limits armed forces before 2027",
                 description="", url="https://polymarket.com/b", outcome="YES",
                 price=0.18, close_time=datetime.now(timezone.utc) + timedelta(days=180))
                 
    result = math_pre_filter(m_a, m_b, check_logical_implication=True)
    assert result.arbitrage_type == "logical_implication"
    assert result.has_arbitrage is False
