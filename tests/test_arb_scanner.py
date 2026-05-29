import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.arb_scanner import _quick_pair_check, find_complementary_pairs
from core.math_filter import FilterDecision, MathFilterResult
from core.models import Market

def _make_market(id: str, title: str, price: float, platform: str = "polymarket") -> Market:
    return Market(
        id=id, platform=platform, title=title,
        description="", url=f"https://poly.market/{id}",
        outcome="YES", price=price,
        close_time=datetime.now(timezone.utc) + timedelta(days=14)
    )

# --- _quick_pair_check ---

def test_quick_pair_check_common_words():
    assert _quick_pair_check("Bitcoin above 100K by December", "Bitcoin above 120K by December") is True

def test_quick_pair_check_no_common():
    assert _quick_pair_check("Trump wins election", "Bitcoin price 2026") is False

def test_quick_pair_check_only_stopwords():
    # 'will', 'the', 'for' — все в STOPWORDS, 0 общих смысловых слов
    assert _quick_pair_check("Will the rate", "For the win") is False

def test_quick_pair_check_min_common_param():
    # "bitcoin above" — 2 общих слова, min_common=3 → False
    assert _quick_pair_check("bitcoin above 5000", "bitcoin above 6000", min_common=3) is False
    assert _quick_pair_check("bitcoin above 5000", "bitcoin above 6000", min_common=2) is True

# --- find_complementary_pairs ---

def test_find_complementary_confirmed_arbitrage():
    """Пара с complementary_overpriced (сумма > 1) → CONFIRMED_ARBITRAGE."""
    a = _make_market("a", "Will Democrat win 2026 election", 0.60)
    b = _make_market("b", "Will Republican win 2026 election", 0.55)
    # sum=1.15 > 1.03 → арбитраж
    results = find_complementary_pairs([a, b], min_spread_pct=5.0)
    assert len(results) == 1
    _, _, mf = results[0]
    assert mf.decision == FilterDecision.CONFIRMED_ARBITRAGE

def test_find_pairs_respects_min_spread():
    """Пары ниже min_spread не включаются."""
    a = _make_market("a", "Bitcoin above 100K December", 0.50)
    b = _make_market("b", "Bitcoin above 110K December", 0.52)
    # spread = 2% < 5% default → не включать
    results = find_complementary_pairs([a, b], min_spread_pct=5.0)
    assert all(mf.spread_pct >= 5.0 for _, _, mf in results)

def test_find_pairs_max_pairs_limit():
    """max_pairs ограничивает результат."""
    markets = [_make_market(str(i), f"Democrat win state{i} election 2026", 0.60 + i*0.01)
               for i in range(20)]
    results = find_complementary_pairs(markets, max_pairs=3)
    assert len(results) <= 3

def test_find_pairs_sorted_by_spread_desc():
    """Результаты отсортированы по убыванию спреда."""
    a = _make_market("a", "Democrat wins big election 2026", 0.65)
    b = _make_market("b", "Republican wins big election 2026", 0.60)
    c = _make_market("c", "Democrat wins big election 2026", 0.70)
    d = _make_market("d", "Republican wins big election 2026", 0.55)
    results = find_complementary_pairs([a, b, c, d])
    spreads = [mf.spread_pct for _, _, mf in results]
    assert spreads == sorted(spreads, reverse=True)

def test_find_pairs_no_llm_called(monkeypatch):
    """Убеждаемся что LLM не вызывается."""
    import agents.shared.utils.gemini_client as gc
    monkeypatch.setattr(gc, "generate_content_with_fallback",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("LLM called!")))
    a = _make_market("a", "Bitcoin election above 100K", 0.50)
    b = _make_market("b", "Bitcoin election above 120K", 0.45)
    # Не должно бросить AssertionError
    find_complementary_pairs([a, b])

def test_find_pairs_handles_math_filter_exception(monkeypatch):
    """Ошибка в math_pre_filter не роняет весь сканер."""
    import core.arb_scanner as scanner
    monkeypatch.setattr(scanner, "math_pre_filter", lambda *a, **kw: (_ for _ in ()).throw(ValueError("boom")))
    a = _make_market("a", "Bitcoin above 100K December", 0.50)
    b = _make_market("b", "Bitcoin above 120K December", 0.45)
    results = find_complementary_pairs([a, b])  # не должно упасть
    assert results == []
