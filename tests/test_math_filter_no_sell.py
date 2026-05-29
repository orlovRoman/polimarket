# tests/test_math_filter_no_sell.py
import pytest
from datetime import datetime, timezone, timedelta
from core.math_filter import math_pre_filter, FilterDecision, validate_trade_instruction
from core.models import Market

def _mkt(id, title, price, platform="polymarket"):
    return Market(id=id, platform=platform, title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=10))

def test_ambiguous_monotonicity_no_sell_in_instruction():
    """AMBIGUOUS monotonicity_violation не содержит SELL YES/NO."""
    # same_event=False: разные кварталы
    a = _mkt("a", "S&P 500 above 5500 in Q1 2026", 0.65)
    b = _mkt("b", "S&P 500 above 5000 in Q4 2026", 0.45)
    mf = math_pre_filter(a, b)
    assert mf.decision == FilterDecision.AMBIGUOUS
    instr = (mf.trade_instruction or "").upper()
    assert "SELL YES" not in instr, f"trade_instruction содержит SELL YES: {mf.trade_instruction}"
    assert "SELL NO"  not in instr, f"trade_instruction содержит SELL NO: {mf.trade_instruction}"

def test_all_trade_instructions_pass_validate():
    """ВСЕ trade_instruction из math_pre_filter проходят validate_trade_instruction."""
    pairs = [
        # complementary_overpriced
        (_mkt("a", "Democrat wins 2026 election", 0.62),
         _mkt("b", "Republican wins 2026 election", 0.55)),
        # monotonicity_violation same_event → CONFIRMED_ARBITRAGE
        (_mkt("c", "Bitcoin above 100000 by December 2026", 0.70),
         _mkt("d", "Bitcoin above 80000 by December 2026", 0.45)),
        # monotonicity_violation same_event=False → AMBIGUOUS
        (_mkt("e", "S&P 500 above 5500 Q1 2026", 0.65),
         _mkt("f", "S&P 500 above 5000 Q4 2026", 0.45)),
    ]
    for a, b in pairs:
        mf = math_pre_filter(a, b)
        if mf.trade_instruction and mf.trade_instruction.strip():
            is_valid, reason = validate_trade_instruction(mf.trade_instruction)
            assert is_valid, (
                f"trade_instruction невалиден для пары ({a.title} | {b.title}):\n"
                f"  instruction: {mf.trade_instruction}\n"
                f"  reason: {reason}"
            )

def test_confirmed_arb_instruction_valid():
    """CONFIRMED_ARBITRAGE всегда имеет валидный trade_instruction."""
    a = _mkt("a", "Democrat wins 2026 midterm election", 0.65)
    b = _mkt("b", "Republican wins 2026 midterm election", 0.60)
    mf = math_pre_filter(a, b)
    if mf.decision == FilterDecision.CONFIRMED_ARBITRAGE:
        is_valid, reason = validate_trade_instruction(mf.trade_instruction)
        assert is_valid, f"CONFIRMED_ARBITRAGE с невалидным instruction: {reason}"
        assert mf.has_arbitrage is True
