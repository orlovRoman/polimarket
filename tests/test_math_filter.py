import pytest
from datetime import datetime, timezone
import itertools
from core.models import Market
from core.math_filter import math_pre_filter, FilterDecision, MathFilterResult, validate_trade_instruction

_id_counter = itertools.count(1)

def make_market(title: str, price: float, platform: str = "polymarket",
                url: str = "https://polymarket.com/test") -> Market:
    return Market(
        id=f"test_{next(_id_counter)}", platform=platform, title=title, url=url,
        description="",
        outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

def test_math_filter_imports_without_error():
    import core.math_filter
    assert hasattr(core.math_filter, 'validate_trade_instruction')

def test_parse_threshold_accessible():
    from core.math_filter import _parse_threshold
    assert _parse_threshold("S&P 500 above 6000") is not None

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
    assert "BUY YES" in result.trade_instruction

def test_same_thresholds_different_platforms():
    a = make_market("SpaceX IPO above $3T", 0.12, platform="polymarket")
    b = make_market("SpaceX IPO above $3T", 0.22, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.arbitrage_type == "price_divergence"
    assert result.decision == FilterDecision.AMBIGUOUS

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
    m_b = Market(id="b", platform="polymarket", title="Russia Ukraine ceasefire before 2027",
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
    m_b = Market(id="b", platform="polymarket", title="Russia Ukraine ceasefire before 2027",
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
    m_b = Market(id="b", platform="polymarket", title="Russia Ukraine ceasefire before 2027",
                 description="", url="https://polymarket.com/b", outcome="YES",
                 price=0.18, close_time=datetime.now(timezone.utc) + timedelta(days=180))
                 
    result = math_pre_filter(m_a, m_b, check_logical_implication=True)
    assert result.arbitrage_type == "logical_implication"
    assert result.has_arbitrage is False


# ── Баг #1: identical threshold → AMBIGUOUS, не ложный арбитраж ─

def test_identical_threshold_returns_no_arbi():
    a = make_market("Will GDP exceed $50B?", 0.6)
    b = make_market("Will revenue exceed $50B?", 0.4)
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "identical_threshold"
    assert result.has_arbitrage is False


def test_identical_threshold_no_false_arbitrage():
    """Два рынка с одним порогом не должны давать CONFIRMED_ARBITRAGE"""
    a = make_market("Will inflation exceed 5%?", 0.7)
    b = make_market("Will CPI exceed 5%?", 0.3)
    result = math_pre_filter(a, b)
    assert result.decision != FilterDecision.CONFIRMED_ARBITRAGE


def test_different_thresholds_still_works():
    """Разные пороги — нормальный Monotonicity path"""
    a = make_market("Will S&P exceed 6000?", 0.3)
    b = make_market("Will S&P exceed 5000?", 0.7)
    result = math_pre_filter(a, b)
    # P(>6000) < P(>5000) — монотонность соблюдена
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI


def test_monotonicity_violation_detected():
    """P(>6000) > P(>5000) — нарушение"""
    a = make_market("Will S&P exceed 6000?", 0.8)
    b = make_market("Will S&P exceed 5000?", 0.3)
    result = math_pre_filter(a, b)
    assert result.arbitrage_type == "monotonicity_violation"
    assert result.spread_pct > 0


# ── Баг #2: мёртвый код validate для underpriced ─────────────

def test_complementary_underpriced_no_forbidden_ops():
    """BUY YES операции — validate всегда пропускает"""
    is_valid, reason = validate_trade_instruction(
        "BUY YES на [Market A] (40¢) + BUY YES на [Market B] (45¢)"
    )
    assert is_valid is True


def test_complementary_underpriced_no_sell_in_instruction():
    """Underpriced инструкция никогда не содержит SELL"""
    a = make_market("Will Trump win?", 0.35)
    b = make_market("Will Harris win?", 0.30)
    result = math_pre_filter(a, b)
    if result.trade_instruction:
        assert "SELL" not in result.trade_instruction.upper()
        assert "SHORT" not in result.trade_instruction.upper()


def test_validate_rejects_sell():
    is_valid, reason = validate_trade_instruction("SELL YES на [Market A]")
    assert is_valid is False
    assert "шорт" in reason.lower() or "sell" in reason.lower() or "недопустима" in reason.lower()


def test_validate_rejects_short():
    is_valid, _ = validate_trade_instruction("SHORT Market B at 60¢")
    assert is_valid is False


def test_validate_accepts_buy_yes():
    is_valid, _ = validate_trade_instruction("BUY YES at 45¢")
    assert is_valid is True


def test_validate_accepts_buy_no():
    is_valid, _ = validate_trade_instruction("BUY NO at 55¢")
    assert is_valid is True


# ── Баг #3: динамический год-фильтр ──────────────────────────

def test_parse_threshold_ignores_current_year():
    from datetime import datetime
    from core.math_filter import _parse_threshold
    year = datetime.now().year
    result = _parse_threshold(f"Will something happen in {year}?")
    assert result is None, f"Год {year} должен быть отфильтрован"


def test_parse_threshold_ignores_near_future_year():
    from datetime import datetime
    from core.math_filter import _parse_threshold
    year = datetime.now().year + 5
    result = _parse_threshold(f"Will something happen by {year}?")
    assert result is None


def test_parse_threshold_ignores_past_year():
    from datetime import datetime
    from core.math_filter import _parse_threshold
    year = datetime.now().year - 1
    result = _parse_threshold(f"Did something happen in {year}?")
    assert result is None


def test_parse_threshold_parses_index_number():
    """6000 не год — должен парситься как pts"""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will S&P exceed 6000?")
    assert result is not None
    assert result == (6000.0, 'pts')


def test_parse_threshold_parses_currency():
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will GDP exceed $1.5T?")
    assert result == (1.5e12, 'usd')


def test_parse_threshold_parses_percentage():
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will inflation exceed 4.5%?")
    assert result == (4.5, '%')


def test_parse_threshold_returns_none_for_no_number():
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will it rain tomorrow?")
    assert result is None


# ── Регрессия: MathFilterResult frozen dataclass ─────────────

def test_math_filter_result_is_frozen():
    r = MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="test",
        spread_pct=0.0,
        reasoning="test",
        trade_instruction=""
    )
    with pytest.raises((AttributeError, TypeError)):
        r.has_arbitrage = True  # frozen=True — нельзя менять


def test_fallback_always_returns_result():
    """Функция никогда не возвращает None"""
    a = make_market("Some market", 0.5)
    b = make_market("Another market", 0.5)
    result = math_pre_filter(a, b)
    assert result is not None
    assert isinstance(result, MathFilterResult)
