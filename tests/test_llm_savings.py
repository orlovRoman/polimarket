"""
Проверяет что ≤ 40% пар доходят до LLM.
Запускать отдельно: pytest tests/test_llm_savings.py -v -s
"""
import pytest
from datetime import datetime, timezone
from core.models import Market
from core.math_filter import math_pre_filter, FilterDecision

def make_synthetic_pairs():
    """100 пар: 50 threshold-пар (часть с нарушениями), 50 price-divergence разного размера."""
    pairs = []
    close = datetime(2026, 12, 31, tzinfo=timezone.utc)

    def m(title, price, platform="polymarket"):
        return Market(id=title[:8], platform=platform, title=title,
                      url="https://x.com", outcome="YES", price=price,
                      close_time=close)

    # 25 threshold-пар с соблюдённой монотонностью → должны быть NO_ARBI
    for i in range(25):
        pairs.append((
            m(f"Market above $3T #{i}", 0.10 + i*0.005),
            m(f"Market above $1T #{i}", 0.70 + i*0.005),
        ))

    # 15 threshold-пар с нарушением → должны быть CONFIRMED_ARBITRAGE
    for i in range(15):
        pairs.append((
            m(f"Market above $5T #{i}", 0.80),
            m(f"Market above $1T #{i}", 0.60),
        ))

    # 40 кросс-платформенных с маленьким спредом → должны быть NO_ARBI
    for i in range(40):
        pairs.append((
            m(f"Fed decision #{i}", 0.50, "polymarket"),
            m(f"Fed decision #{i}", 0.52, "kalshi"),
        ))

    # 20 кросс-платформенных с большим спредом → должны быть AMBIGUOUS (идут в LLM)
    for i in range(20):
        pairs.append((
            m(f"Election outcome #{i}", 0.35, "polymarket"),
            m(f"Election outcome #{i}", 0.60, "kalshi"),
        ))

    return pairs

def test_llm_savings_rate():
    pairs = make_synthetic_pairs()
    ambiguous_count = sum(
        1 for a, b in pairs
        if math_pre_filter(a, b).decision == FilterDecision.AMBIGUOUS
    )
    total = len(pairs)
    rate = ambiguous_count / total
    print(f"\nLLM savings: {total - ambiguous_count}/{total} пар отфильтровано "
          f"({(1-rate)*100:.1f}% экономия)")
    print(f"До LLM доходит: {ambiguous_count}/{total} ({rate*100:.1f}%)")
    assert rate <= 0.40, (
        f"Слишком много пар идёт в LLM: {rate*100:.1f}% > 40%. "
        f"Проверь пороги фильтра."
    )
