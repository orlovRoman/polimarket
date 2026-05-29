# tests/test_math_filter_new_cases.py

import pytest
from datetime import datetime, timezone
from core.math_filter import (
    math_pre_filter, _check_same_event, _parse_threshold,
    FilterDecision, MathFilterResult
)
from core.models import Market

def _m(title, price, platform="polymarket", mid=None):
    return Market(
        id=mid or f"id_{hash(title) % 10000}",
        platform=platform, title=title,
        url=f"https://polymarket.com/{hash(title)}",
        description="", outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )


# ── Fix 1: s&p токенизация ───────────────────────────────────────────────────

def test_check_same_event_sp500_vs_s_and_p_500():
    """BUG-1: 'S&P 500 above 6000' и 'SP500 above 6000' — одно событие"""
    assert _check_same_event("Will S&P 500 close above 6000?", "Will SP500 close above 6000?") is True

def test_check_same_event_spx_vs_sp500():
    """SPX vs SP500 — одно событие через алиасы"""
    assert _check_same_event("Will SPX exceed 5500?", "Will SP500 exceed 5500?") is True

def test_check_same_event_btc_vs_sp500():
    """BTC и S&P 500 — разные активы"""
    assert _check_same_event("Will BTC hit $200K?", "Will SP500 close above 6000?") is False


# ── Fix 2: год только в одном заголовке ─────────────────────────────────────

def test_check_same_event_year_in_one_title_only():
    """
    BUG-2: 'BTC hit $100K by 2026' vs 'BTC hit $100K' 
    Год только в первом → allow_different_dates=False должен отсекать
    """
    result = _check_same_event(
        "Will BTC hit $100K by 2026?",
        "Will BTC hit $100K?",
        allow_different_dates=False
    )
    # С фиксом: если год в одном — они не совпадают → False
    assert result is False

def test_check_same_event_different_years_both_present():
    """Разные годы в обоих → False"""
    assert _check_same_event(
        "Will BTC hit $100K by 2026?",
        "Will BTC hit $100K by 2027?",
        allow_different_dates=False
    ) is False

def test_check_same_event_same_year_both_present():
    """Одинаковый год в обоих → не отсекается по году"""
    result = _check_same_event(
        "Will BTC hit $100K by 2026?",
        "Will BTC reach $100K in 2026?",
        allow_different_dates=False
    )
    assert result is True  # Один год → одно событие

def test_check_same_event_different_quarters():
    """Разные кварталы → False"""
    assert _check_same_event(
        "Will Fed cut rates in Q1?",
        "Will Fed cut rates in Q3?",
        allow_different_dates=False
    ) is False

def test_check_same_event_allow_different_dates_ignores_year():
    """allow_different_dates=True → год не проверяется (для logical_implication)"""
    result = _check_same_event(
        "Russia Ukraine ceasefire by Oct 2026",
        "Russia Ukraine ceasefire before 2027",
        allow_different_dates=True
    )
    assert result is True


# ── time_markers фильтр ──────────────────────────────────────────────────────

def test_check_same_event_time_markers_not_counted_as_keywords():
    """
    'Will BTC hit $100K in January?' vs 'Will BTC hit $100K in December?'
    Месяцы удаляются time_markers — не должны влиять на overlap
    """
    result = _check_same_event(
        "Will BTC hit $100K in January?",
        "Will BTC hit $100K in December?"
    )
    # Оба одно событие (BTC $100K), просто разные месяцы
    # После time_markers оба дают примерно {'bitcoin'} → True
    assert result is True

def test_check_same_event_quarterly_filtered():
    """q1, q2 в stopwords → не влияют на overlap"""
    r1 = _check_same_event("Will inflation exceed 5% in Q1?", "Will inflation exceed 5% in Q2?", allow_different_dates=True)
    r2 = _check_same_event("Will inflation exceed 5%?", "Will inflation exceed 5%?", allow_different_dates=True)
    # Оба должны быть одинаково True
    assert r1 == r2


# ── Identical threshold cross-platform edge cases ────────────────────────────

def test_identical_threshold_different_assets_cross_platform_rejected():
    """
    BUG-3: BTC $100K (Polymarket) vs ETH $100K (Kalshi)
    same_event=False, platform разные → CONFIRMED_NO_ARBI (правильно)
    """
    a = _m("Will BTC hit $100K?", 0.45, platform="polymarket")
    b = _m("Will ETH hit $100K?", 0.10, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "identical_threshold"

def test_identical_threshold_same_asset_cross_platform_goes_to_price_divergence():
    """
    BTC $100K Polymarket vs BTC $100K Kalshi
    same_event=True, platform разные → price_divergence, не identical_threshold
    """
    a = _m("Will BTC hit $100K?", 0.45, platform="polymarket")
    b = _m("Will Bitcoin reach $100K?", 0.62, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.arbitrage_type == "price_divergence"
    assert result.decision == FilterDecision.AMBIGUOUS


# ── make_market id уникальность ──────────────────────────────────────────────

def test_make_market_unique_ids():
    """Фабрика рынков должна давать уникальные id"""
    from tests.test_math_filter import make_market
    m1 = make_market("Market A", 0.5)
    m2 = make_market("Market B", 0.6)
    assert m1.id != m2.id


# ── SELL_YES fix в agent CONFIRMED_ARBITRAGE path ────────────────────────────

def test_confirmed_arbitrage_price_divergence_no_sell_in_math_result():
    """
    price_divergence cross-platform всегда AMBIGUOUS — 
    не может попасть в CONFIRMED_ARBITRAGE с SELL_YES
    """
    a = _m("Will Fed cut rates?", 0.40, platform="polymarket")
    b = _m("Will Fed cut rates?", 0.65, platform="kalshi")
    result = math_pre_filter(a, b)
    # price_divergence → AMBIGUOUS, не CONFIRMED_ARBITRAGE
    assert result.decision == FilterDecision.AMBIGUOUS
    assert result.decision != FilterDecision.CONFIRMED_ARBITRAGE
    # trade_instruction пустая — без SELL
    assert "SELL" not in result.trade_instruction.upper()


# ── Regression: 50% threshold ────────────────────────────────────────────────

def test_threshold_50_pct_exact_boundary():
    """Ровно 50% overlap → True (граничное значение >= 0.50)"""
    # 2 общих слова из 4 = 50%
    result = _check_same_event("bitcoin ethereum price rally", "bitcoin ripple token rally")
    # 'bitcoin' + 'rally' общие из min(4,4)=4 → 50% → True
    assert result is True

def test_threshold_below_50_pct_different_events():
    """Менее 50% overlap → False"""
    assert _check_same_event(
        "Will Apple stock hit $300?",
        "Will Tesla stock hit $300?"
    ) is False  # только '$300' (без цифр = ничего) + 'stock' → мало общего
