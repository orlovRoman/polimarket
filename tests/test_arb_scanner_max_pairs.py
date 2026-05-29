# tests/test_arb_scanner_max_pairs.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from core.arb_scanner import find_complementary_pairs
from core.math_filter import FilterDecision, MathFilterResult
from core.models import Market

def _mkt(id, title, price):
    return Market(id=id, platform="polymarket", title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=14))

def _arb_mf(spread=15.0):
    return MathFilterResult(
        decision=FilterDecision.CONFIRMED_ARBITRAGE,
        arbitrage_type="complementary_overpriced",
        spread_pct=spread, reasoning="test", trade_instruction="BUY NO",
        has_arbitrage=True,
    )

# --- max_pairs строго соблюдается ---

def test_max_pairs_never_exceeded(monkeypatch):
    """find_complementary_pairs НИКОГДА не возвращает > max_pairs."""
    markets = [_mkt(str(i), f"Bitcoin above {50000+i*1000} December 2026", 0.50 + i*0.01)
               for i in range(30)]
    # Мокируем math_pre_filter чтобы ВСЕ пары давали CONFIRMED_ARBITRAGE
    import core.arb_scanner as scanner
    monkeypatch.setattr(scanner, "math_pre_filter", lambda *a, **kw: _arb_mf(15.0))

    for max_p in [1, 3, 5, 10]:
        results = find_complementary_pairs(markets, max_pairs=max_p)
        assert len(results) <= max_p, f"max_pairs={max_p} нарушен: получено {len(results)}"

def test_max_pairs_guard_fires_at_outer_loop(monkeypatch):
    """После накопления max_pairs внешний цикл тоже прерывается."""
    markets = [_mkt(str(i), f"Bitcoin above {50000+i*1000} December 2026", 0.50)
               for i in range(20)]
    import core.arb_scanner as scanner
    call_count = {"n": 0}
    def counting_filter(a, b):
        call_count["n"] += 1
        return _arb_mf(15.0)
    monkeypatch.setattr(scanner, "math_pre_filter", counting_filter)
    monkeypatch.setattr(scanner, "_quick_pair_check", lambda *a, **kw: True)

    find_complementary_pairs(markets, max_pairs=3)
    # С правильным early exit вызовов math_pre_filter должно быть ровно 3
    assert call_count["n"] == 3, f"math_pre_filter вызван {call_count['n']} раз, ожидалось 3"

def test_results_sorted_descending(monkeypatch):
    """Результаты отсортированы по убыванию spread_pct."""
    markets = [_mkt(str(i), f"S&P 500 above {5000+i*100} end 2026", 0.50)
               for i in range(10)]
    spreads = [25.0, 10.0, 18.0, 7.0, 30.0]
    call_count = {"n": 0}
    import core.arb_scanner as scanner
    def spread_mock(a, b):
        s = spreads[call_count["n"] % len(spreads)]
        call_count["n"] += 1
        return MathFilterResult(
            decision=FilterDecision.CONFIRMED_ARBITRAGE,
            arbitrage_type="monotonicity_violation",
            spread_pct=s, reasoning="", trade_instruction="BUY NO", has_arbitrage=True,
        )
    monkeypatch.setattr(scanner, "math_pre_filter", spread_mock)
    monkeypatch.setattr(scanner, "_quick_pair_check", lambda *a, **kw: True)

    results = find_complementary_pairs(markets, max_pairs=5)
    sp = [r[2].spread_pct for r in results]
    assert sp == sorted(sp, reverse=True), f"Порядок нарушен: {sp}"
