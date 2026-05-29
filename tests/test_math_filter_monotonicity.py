import pytest
from datetime import datetime, timezone, timedelta
from core.math_filter import math_pre_filter, FilterDecision
from core.models import Market

def _mkt(id, title, price):
    return Market(id=id, platform="polymarket", title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=10))

# --- Новое поведение: same_event → CONFIRMED_ARBITRAGE ---

def test_monotonicity_same_event_confirmed_arbitrage():
    """
    S&P 500 above 5500 стоит дороже S&P 500 above 5000 —
    одно событие → CONFIRMED_ARBITRAGE.
    """
    a = _mkt("a", "Will S&P 500 close above 5500 in 2026?", 0.65)
    b = _mkt("b", "Will S&P 500 close above 5000 in 2026?", 0.50)
    mf = math_pre_filter(a, b)
    assert mf.decision == FilterDecision.CONFIRMED_ARBITRAGE
    assert mf.arbitrage_type == "monotonicity_violation"
    assert mf.has_arbitrage is True
    assert "BUY YES" in mf.trade_instruction

def test_monotonicity_same_event_spread_in_result():
    """spread_pct корректно отражает расхождение."""
    a = _mkt("a", "Bitcoin above 100000 by December 2026", 0.70)
    b = _mkt("b", "Bitcoin above 80000 by December 2026", 0.50)
    mf = math_pre_filter(a, b)
    assert mf.decision == FilterDecision.CONFIRMED_ARBITRAGE
    assert abs(mf.spread_pct - 20.0) < 0.1  # (0.70 - 0.50) * 100

# --- Старое поведение сохраняется: разные события → AMBIGUOUS ---

def test_monotonicity_different_events_stays_ambiguous():
    """
    'above 5500 in Q1' vs 'above 5000 in Q4' — разные кварталы,
    _check_same_event вернёт False → остаётся AMBIGUOUS.
    """
    a = _mkt("a", "Will S&P 500 close above 5500 in Q1 2026?", 0.65)
    b = _mkt("b", "Will S&P 500 close above 5000 in Q4 2026?", 0.50)
    mf = math_pre_filter(a, b)
    # Разные кварталы → не одно событие
    assert mf.decision == FilterDecision.AMBIGUOUS

def test_monotonicity_spread_below_threshold_no_arb():
    """Малый спред → CONFIRMED_NO_ARBI независимо от same_event."""
    a = _mkt("a", "Bitcoin above 100000 by December 2026", 0.52)
    b = _mkt("b", "Bitcoin above 90000 by December 2026", 0.50)
    mf = math_pre_filter(a, b, min_spread_pct=5.0)
    assert mf.decision == FilterDecision.CONFIRMED_NO_ARBI

def test_monotonicity_correct_direction_no_arb():
    """Монотонность соблюдена (дешёвый порог дороже) → нет арбитража."""
    a = _mkt("a", "Bitcoin above 100000 by December 2026", 0.40)
    b = _mkt("b", "Bitcoin above 80000 by December 2026", 0.60)
    mf = math_pre_filter(a, b)
    assert mf.decision == FilterDecision.CONFIRMED_NO_ARBI

def test_trade_instruction_no_sell():
    """trade_instruction никогда не содержит SELL — шорт невозможен."""
    a = _mkt("a", "S&P 500 above 5500 December 2026", 0.65)
    b = _mkt("b", "S&P 500 above 5000 December 2026", 0.45)
    mf = math_pre_filter(a, b)
    assert "SELL" not in mf.trade_instruction.upper()
